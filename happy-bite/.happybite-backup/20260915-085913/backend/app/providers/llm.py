"""Language model, behind three methods.

    stream(system, messages)             -> async iterator of text   (plain chat)
    chat_actions(system, messages, ...)  -> async iterator of events (chat + tools)
    json(system, user, schema)           -> dict                     (recipes, filters)

Two implementations. Anthropic is the default. "openai" speaks the OpenAI
chat protocol, which also covers a model running on this machine through
Ollama — the fully offline option the product was built around, and the
target the fine-tuned Qwen is meant to slot into.

`chat_actions` runs the tool loop. The model may ask to change the kitchen
(lower the milk, save a recipe, set a timer); each request is validated by
the caller's `normalize` and, if clean, yielded as an {"type":"action"}
event for the browser to carry out. The tool_result fed back to the model is
a short synthetic acknowledgement — the browser is the real executor — so the
model can finish its sentence naturally after acting.
"""

import json
import re
from collections.abc import AsyncIterator

from ..config import Settings

TOOL_ROUNDS = 5          # hard stop on a model that keeps calling tools


class LLMError(RuntimeError):
    pass


def tidy(messages: list[dict]) -> list[dict]:
    """Alternating user/assistant, starting with the user. Both APIs want
    that, and a chat UI doesn't naturally guarantee it. Only plain-text turns;
    tool_use/tool_result blocks are added later, inside the loop."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        text = str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        if not out and role == "assistant":
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    return out


THINK_RX = re.compile(r"<think>.*?</think>\s*", re.S | re.I)
OPEN_THINK_RX = re.compile(r"<think>.*\Z", re.S | re.I)
CLOSE_ONLY_RX = re.compile(r"\A.*?</think>\s*", re.S | re.I)


def strip_think(text: str) -> str:
    """Drop a reasoning block before anyone — or anything — reads the reply.

    A hybrid-reasoning model may answer with its working in front of the real
    text. Two reasons this cannot be left in. The browser would display the
    model talking to itself; worse, `parse_json` below takes everything between
    the first `{` and the last `}`, and a page of deliberation that mentions
    JSON breaks recipe parsing outright.

    THREE shapes, and the third is the common one:

      <think>…</think>answer   both tags — the textbook case
      <think>…                 no close: truncated, or max_tokens hit mid-thought
      …</think>answer          **no OPEN tag at all**

    The last one looks like a server bug and isn't. Qwen's chat template puts
    the opening `<think>` at the END OF THE PROMPT, so the assistant turn starts
    already inside the block and the model only ever writes the closing tag.
    The version of this function that began `if "<think>" not in text: return
    text` therefore did nothing at all for the most common output in the wild,
    and handed the model's private reasoning straight to the JSON parser —
    which is exactly what "No JSON in the reply" was.
    """
    low = text.lower()
    if "</think>" in low and "<think>" not in low:
        return CLOSE_ONLY_RX.sub("", text, count=1).strip()
    if "<think>" not in low:
        return text
    return OPEN_THINK_RX.sub("", THINK_RX.sub("", text)).strip()


def parse_json(text: str) -> dict:
    clean = re.sub(r"```(?:json)?", "", strip_think(text)).strip()
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end < 0:
        # Say what it DID say. "No JSON in the reply" is the third error in this
        # codebase to name a category and withhold the evidence — after a bare
        # ModuleNotFoundError and a bare BadRequestError, each of which cost a
        # day. The reply itself is the diagnosis; print it.
        if not clean and "<think>" in text.lower():
            raise LLMError(
                "the reply was nothing but a reasoning block — the model used its "
                "whole token budget thinking and never reached the answer. Turn "
                "reasoning off in the model server's own settings; see docs/MODEL.md.")
        shown = (clean or text).strip()[:200]
        raise LLMError(f"no JSON in the reply. It said: {shown!r}" if shown
                       else "the reply was entirely empty.")
    blob = clean[start:end + 1]
    try:
        return json.loads(blob)
    except ValueError as e:
        # A recipe that ran out of room stops mid-sentence, so the last `}` in
        # it belongs to some nested object — an ingredient, a step. Slicing to
        # that brace produces something that looks like JSON and fails deep
        # inside, which is how "Expecting ',' delimiter: line 160 column 6"
        # happens. json's positional complaint is true and useless; the useful
        # fact is that the reply never finished.
        #
        # Deliberately NOT repaired by closing the open brackets. That would
        # yield a recipe whose last steps are missing, and a method that stops
        # before the dish is cooked is worse than no method at all.
        unclosed = blob.count("{") - blob.count("}")
        tail = blob[-100:].replace("\n", " ").strip()
        if unclosed > 0:
            raise LLMError(
                f"the recipe was cut off before it finished — {len(blob)} characters "
                f"of JSON with {unclosed} bracket(s) still open. It ends: …{tail!r}. "
                "The model ran out of room; if reasoning is on, it spent the budget "
                "thinking first. See docs/MODEL.md.") from None
        raise LLMError(f"the reply wasn't valid JSON ({e}). It ends: …{tail!r}") from None


def _openai_tools(tools: list[dict]) -> list[dict]:
    """Anthropic-native tool dicts -> OpenAI/Ollama function-calling shape."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in tools]


class AnthropicLLM:
    def __init__(self, s: Settings):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=s.anthropic_api_key)
        self.model = s.llm_model
        self.name = f"anthropic:{s.llm_model}"

    async def stream(self, system: str, messages: list[dict], max_tokens: int = 900) -> AsyncIterator[str]:
        async with self.client.messages.stream(
            model=self.model, max_tokens=max_tokens, system=system, messages=tidy(messages),
        ) as s:
            async for text in s.text_stream:
                yield text

    async def chat_actions(self, system, messages, tools, normalize, known, max_tokens=900):
        work = tidy(messages)
        for _ in range(TOOL_ROUNDS):
            async with self.client.messages.stream(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=work, tools=tools,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"type": "delta", "text": text}
                final = await stream.get_final_message()

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                return
            work.append({"role": "assistant", "content": [b.model_dump() for b in final.content]})
            results = []
            for tu in tool_uses:
                action, ack = normalize(tu.name, dict(tu.input), known)
                if action is not None:
                    yield {"type": "action", "action": action}
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": ack})
            work.append({"role": "user", "content": results})

    async def json(self, system: str, user: str, schema: dict, max_tokens: int = 2400) -> dict:
        msg = await self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": "answer", "description": "Return the result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": "answer"},
        )
        for block in msg.content:
            if block.type == "tool_use":
                return dict(block.input)
        raise LLMError("The model didn't return structured output")


def _extra_body(s: Settings) -> list[tuple[str, dict]]:
    """The non-standard fields, as separate layers, ordered most expendable first.

    A list rather than one dict, and the order is the whole point. Every local
    server claims to speak "the OpenAI protocol" and each means something
    different by it: Ollama ignores fields it doesn't know, LM Studio validates
    the body and answers 400. So `_create` gives these up one at a time rather
    than all together — because they are very far from equally important.

    `keep_alive` is an Ollama word for how long to hold the model in memory.
    Losing it against another server costs nothing.

    `chat_template_kwargs` is last because it is the one that matters. It
    carries `enable_thinking`, and Qwen 3.5 has thinking ON by default: with it
    on, this model spent **1553 seconds reasoning** before writing one word of
    a recipe. Dropping this field alongside `keep_alive` — which is exactly what
    the previous version did — turned a 400 into a twenty-six-minute wait, which
    is a far worse failure than the error it was trying to avoid.
    """
    if not getattr(s, "llm_extras", True):
        return []
    layers: list[tuple[str, dict]] = [
        ("keep_alive", {"keep_alive": getattr(s, "llm_keep_alive", "1h")}),
    ]
    if getattr(s, "llm_num_ctx", 0):
        layers.append(("options", {"options": {"num_ctx": int(s.llm_num_ctx)}}))
    layers.append(("chat_template_kwargs", {
        "chat_template_kwargs": {"enable_thinking": bool(getattr(s, "llm_think", False))},
    }))
    return layers


class OpenAICompatLLM:
    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key or "none",
                      base_url=s.openai_base_url or None,
                      # One attempt. A local model is not a flaky network: if it
                      # timed out it is because the machine is too slow for the
                      # prompt, and asking twice more just triples the wait
                      # before the same failure.
                      max_retries=0,
                      timeout=getattr(s, "llm_timeout", 600.0))
        self.model = s.llm_model
        self.name = f"openai:{s.llm_model}" + (" (local)" if s.openai_base_url else "")
        self.extra_layers = _extra_body(s)
        self.temperature = getattr(s, "llm_temperature", 0.7)
        self.top_p = getattr(s, "llm_top_p", 0.8)
        self.json_tokens = int(getattr(s, "llm_json_tokens", 3000) or 0)

    async def _create(self, **kw):
        """One request, surrendering the optional parts if the server objects.

        Every local server speaks "the OpenAI protocol" and every one of them
        means something slightly different by it. Rather than keep a table of
        which server tolerates what — which goes stale the moment anyone
        upgrades — this asks for what it wants, and on a 400 drops the most
        expendable thing and asks again:

            1. everything, including the extra fields
            2. without the extra fields  (they stay off for this process)
            3. without response_format   (the prompt asks for JSON anyway, and
                                          parse_json copes with prose around it)

        A 400 is the server saying "I don't accept that", which is worth acting
        on. Anything else — a timeout, a crash, a refused connection — is not
        about the request shape, so it is raised immediately.
        """
        plans: list[tuple[list, dict]] = []
        layers = list(self.extra_layers)
        while layers:
            merged: dict = {}
            for _, part in layers:
                merged.update(part)
            plans.append((list(layers), dict(kw, extra_body=merged)))
            layers = layers[1:]               # give up the most expendable one
        plans.append(([], dict(kw)))
        if kw.get("response_format"):
            plans.append(([], {k: v for k, v in kw.items() if k != "response_format"}))

        for i, (keep, body) in enumerate(plans):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model, temperature=self.temperature,
                    top_p=self.top_p, **body)
                self.extra_layers = keep      # what this server actually accepts
                return resp
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) != 400 or i == len(plans) - 1:
                    raise
                dropped = keep[0][0] if keep else "response_format"
                print(f"llm: {self.name} answered 400 — dropping {dropped} and asking "
                      f"again. It said: {str(e)[:200]}")
                if dropped == "chat_template_kwargs":
                    # Worth shouting about. Without this field the model decides
                    # for itself whether to reason, and Qwen 3.5 decides yes.
                    print("llm: !! that was the thinking switch. This model will now "
                          "reason before every answer, which can take MINUTES. Turn "
                          "reasoning off in the server's own settings — see docs/MODEL.md.")
        raise RuntimeError("unreachable: every plan returns or raises")

    async def stream(self, system: str, messages: list[dict], max_tokens: int = 900) -> AsyncIterator[str]:
        resp = await self._create(
            max_tokens=max_tokens, stream=True,
            messages=[{"role": "system", "content": system}, *tidy(messages)],
        )
        # A reasoning block arrives token by token, so it cannot be stripped from
        # a finished string. Instead: buffer until we can tell whether the reply
        # opens with one. With thinking off (the default for the 9B) that costs
        # a single token of delay and nothing else.
        #
        # Whitespace is buffered, never dropped — losing the spaces between
        # words is exactly what a naive `if not piece.strip(): continue` does
        # when a server sends one token per character.
        thinking = False          # inside <think>…</think>
        decided = False           # past the point where a block could start
        head = ""                 # held back until one of the above is known
        async for chunk in resp:
            if not (chunk.choices and chunk.choices[0].delta.content):
                continue
            piece = chunk.choices[0].delta.content
            if decided:
                yield piece
                continue
            head += piece
            if thinking:
                cut = re.search(r"</think>", head, re.I)
                if cut:
                    rest = head[cut.end():].lstrip()
                    thinking, decided, head = False, True, ""
                    if rest:
                        yield rest
                continue
            lead = head.lstrip()
            if not lead:
                continue                      # nothing but whitespace so far
            low = lead.lower()
            if low.startswith("<think>"):
                thinking, head = True, lead
                continue
            if len(low) < 7 and "<think>".startswith(low):
                continue                      # could still become "<think>"
            decided = True
            out, head = head, ""
            yield out
        # Whatever is left: an unclosed block (truncated reply, max_tokens
        # reached mid-thought) is cut here, because everything after an
        # unclosed <think> is thinking by definition.
        if head:
            yield strip_think(head)

    async def chat_actions(self, system, messages, tools, normalize, known, max_tokens=900):
        # Non-streaming tool loop. Local servers (Ollama, LM Studio) are far more
        # reliable with tools when NOT streaming — streaming + tools resets the
        # connection on some versions (surfaces as APIConnectionError). The reply
        # therefore arrives as a whole rather than token-by-token; a fair trade
        # for "it actually works offline". Falls back to a plain no-tools call if
        # the model rejects the tools payload.
        work = [{"role": "system", "content": system}, *tidy(messages)]
        oa_tools = _openai_tools(tools)
        for _ in range(TOOL_ROUNDS):
            try:
                completion = await self._create(max_tokens=max_tokens, messages=work,
                                                tools=oa_tools)
            except (TypeError, ValueError):
                completion = await self._create(max_tokens=max_tokens, messages=work)
            m = completion.choices[0].message
            content = strip_think(m.content or "")
            calls = list(getattr(m, "tool_calls", None) or [])
            if content:
                yield {"type": "delta", "text": content}
            if not calls:
                return
            work.append({"role": "assistant", "content": content or None,
                         "tool_calls": [{"id": tc.id or f"call_{i}", "type": "function",
                                         "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                        for i, tc in enumerate(calls)]})
            for i, tc in enumerate(calls):
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                action, ack = normalize(tc.function.name, args, known)
                if action is not None:
                    yield {"type": "action", "action": action}
                work.append({"role": "tool", "tool_call_id": tc.id or f"call_{i}", "content": ack})

    async def json(self, system: str, user: str, schema: dict, max_tokens: int = 2400) -> dict:
        # A floor, never a cap: the caller asks for what it needs and this only
        # ever gives it more room. See llm_json_tokens in config.py.
        max_tokens = max(max_tokens, self.json_tokens)
        resp = await self._create(
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system + "\n\nReply with one JSON object matching this "
                 "JSON Schema, and nothing else:\n" + json.dumps(schema)},
                {"role": "user", "content": user},
            ],
        )
        choice = resp.choices[0]
        message = choice.message
        text = message.content or ""
        finish = getattr(choice, "finish_reason", None)

        # Servers disagree about where reasoning goes. Some inline it as
        # <think>…</think> in the content (strip_think handles those); others
        # put it in its own field and leave content empty. An empty content
        # beside a full reasoning field is not a mysterious failure — it is the
        # model having thought instead of answered, and it should say so.
        thought = (getattr(message, "reasoning_content", None)
                   or getattr(message, "reasoning", None) or "")
        if not text.strip() and thought:
            raise LLMError(
                f"the model returned {len(str(thought))} characters of reasoning and no "
                "answer. Turn reasoning off in the model server's own settings; "
                "see docs/MODEL.md.")

        # `length` means the model was still talking when the budget ran out.
        # That is true whether or not any JSON made it into the reply, and the
        # earlier version only checked the no-JSON case — so a recipe that
        # stopped halfway fell through to the parser and came back as a column
        # number. Truncation is truncation; say so first.
        if finish == "length":
            thinking = "</think>" in text.lower() or bool(thought)
            raise LLMError(
                f"the model hit its {max_tokens}-token limit and "
                + ("never reached the JSON" if "{" not in text else "stopped in the middle of it")
                + (" — it spent the budget reasoning first. Turn reasoning off in the "
                   "model server's own settings; see docs/MODEL.md."
                   if thinking else ". Try again, or ask for something simpler."))

        return parse_json(text)


def make_llm(s: Settings):
    if s.llm_provider == "anthropic" and s.anthropic_api_key:
        return AnthropicLLM(s)
    if s.llm_provider == "openai" and (s.openai_api_key or s.openai_base_url):
        return OpenAICompatLLM(s)
    return None
