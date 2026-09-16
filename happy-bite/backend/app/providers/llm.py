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

WHAT CHANGED, AND WHY THE RECIPE "RAN OUT OF ROOM" WHEN IT HADN'T
-----------------------------------------------------------------
A recipe came back as 1574 characters of JSON ending:

    ... "cue": "Container getting hard", "minutes": 12 " ] }

and the app said the model had run out of room and told the user to lower
LLM_JSON_TOKENS. Both halves of that were wrong, and the evidence was in the
string it printed:

  * A reply that runs out of room stops MID-TOKEN — `"cue": "Container get`.
    This one closes the array and the object. The model thought it had
    finished.
  * `"minutes": 12 "` has a stray quote in it. That is not a budget problem,
    it is a small model writing invalid JSON, which is the single most common
    way a 4-9B model fails at structured output.

`parse_json` decided "cut off" purely from unbalanced brackets, which a stray
quote produces just as readily as truncation does. So the diagnosis was
confident, wrong, and pointed at a setting that had nothing to do with it.

Three changes, in order of how much they matter:

1. **The reply is grammar-constrained where the server can do it.** Ollama,
   llama.cpp and LM Studio all accept `response_format: {"type":"json_schema"}`
   and will then only emit tokens the schema allows — invalid JSON becomes
   impossible rather than unlikely. Tried first, degraded to `json_object` and
   then to nothing on a 400, and what the server accepted is remembered.

2. **Structured output gets its own, much lower temperature.** 0.7 with
   top_p 0.8 is a chat setting; for JSON it is asking the model to be creative
   about punctuation. LLM_JSON_TEMPERATURE defaults to 0.2.

3. **One automatic retry, and an honest error.** If the JSON still doesn't
   parse and the model stopped by itself, that is a bad-output problem, not a
   budget one: it is worth one more go at temperature 0 before giving up, and
   the message now says which of the two actually happened instead of always
   blaming the token limit.
"""

import json
import re
import time
from collections.abc import AsyncIterator

from ..config import Settings

TOOL_ROUNDS = 5          # hard stop on a model that keeps calling tools


def _is_timeout(e: BaseException) -> bool:
    """A request that ran out of clock, whoever is reporting it.

    openai raises APITimeoutError, httpx raises ReadTimeout or
    ConnectTimeout, asyncio raises TimeoutError, and which one surfaces
    depends on versions nobody here controls. They all mean one thing, and
    that one thing deserves a better answer than the class name.
    """
    return any("timeout" in type(x).__name__.lower()
               for x in (e, e.__cause__, e.__context__) if x is not None)


class LLMError(RuntimeError):
    pass


# A 400 that means "this will never fit", not "I don't accept that field".
#
# llama.cpp and everything built on it (LM Studio, llama-server) answer with
#
#     The number of tokens to keep from the initial prompt is greater
#     than the context length
#
# and Ollama, vLLM and the rest each have their own wording for the same
# arithmetic. It is worth catching by hand because the generic 400 handler
# below responds to a refusal by dropping optional fields and asking again —
# which is exactly the wrong move here. Nothing in `keep_alive` is making the
# prompt too long, so all that achieves is three more failures and a log full
# of misleading lines about the thinking switch.
TOO_LONG_RX = re.compile(
    r"context (?:length|window|size)|n_ctx|tokens to keep|"
    r"exceeds? .{0,40}context|prompt is too long|maximum context",
    re.I)

CHARS_PER_TOKEN = 3.5      # English prose; deliberately a slight under-estimate


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


def _looks_truncated(blob: str) -> bool:
    """Did this reply stop mid-sentence, or did the model think it was done?

    The distinction the old code could not make. A reply that hit a ceiling
    ends inside a token — an unclosed string, a bare key, a dangling comma. A
    reply the model finished ends on a closing brace or bracket, however
    malformed the middle of it is.

    Not perfect, and it does not have to be: it decides which SENTENCE the user
    reads, and both sentences now suggest something worth doing.
    """
    tail = blob.rstrip()
    if not tail:
        return True
    if tail.endswith(("}", "]")):
        return False                      # it closed something; it meant to stop
    if tail.endswith((",", ":")):
        return True                       # a dangling key or comma: mid-object
    # An odd number of unescaped quotes means we stopped inside a string.
    return (tail.count('"') - tail.count('\\"')) % 2 == 1


def parse_json(text: str, finish: str | None = None) -> dict:
    clean = re.sub(r"```(?:json)?", "", strip_think(text)).strip()
    start, end = clean.find("{"), clean.rfind("}")

    # An opening brace and no closing one anywhere is the clearest truncation
    # there is, and the old code called it "no JSON in the reply" — which reads
    # as "the model ignored you" when in fact it started correctly and was cut
    # off. Different problem, different fix, so it gets its own sentence.
    if start >= 0 and end < 0:
        tail = clean[-100:].replace("\n", " ").strip()
        raise LLMError(
            f"the reply was cut off before a single object closed — {len(clean) - start} "
            f"characters in, ending: …{tail!r}. Raise LLM_JSON_TOKENS, or turn "
            "reasoning off if it is on; see docs/MODEL.md.")

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
        unclosed = blob.count("{") - blob.count("}")
        tail = blob[-100:].replace("\n", " ").strip()

        # TRUNCATED: the model was still writing. Deliberately NOT repaired by
        # closing the open brackets — that would yield a recipe whose last steps
        # are missing, and a method that stops before the dish is cooked is
        # worse than no method at all.
        if finish == "length" or (unclosed > 0 and _looks_truncated(blob)):
            raise LLMError(
                f"the recipe was cut off while it was still being written — "
                f"{len(blob)} characters of JSON with {unclosed} bracket(s) still "
                f"open. It ends: …{tail!r}. Raise LLM_JSON_TOKENS, or if reasoning "
                "is on, turn it off — it spends the budget before the answer "
                "starts. See docs/MODEL.md.") from None

        # MALFORMED: the model finished and what it wrote isn't valid JSON. A
        # stray quote, a missing comma, a number with a quote stuck to it. This
        # is what small models do, it has nothing to do with the token budget,
        # and telling someone to lower LLM_JSON_TOKENS here sends them to fix a
        # setting that was never involved.
        raise LLMError(
            f"the model finished, but what it wrote isn't valid JSON ({e}). It "
            f"ends: …{tail!r}. Small models do this; the cure is constrained "
            "decoding — make sure your server accepts response_format "
            "json_schema (Ollama, llama.cpp and LM Studio all do), or lower "
            "LLM_JSON_TEMPERATURE.") from None


def inline_tool_calls(text: str, tools: list[dict]) -> tuple[str, list[tuple[str, dict]]]:
    """Tool calls a model wrote INTO its reply instead of as tool calls.

    Gemma's native format is `call:adjust_stock{change:-3,id:<|"|>egg<|"|>}`,
    and LM Studio doesn't always recognise it, so it arrives as ordinary text:
    the person sees that line in the bubble and nothing happens to their eggs.
    About half of gemma-4-e2b's calls came back this way. A bare
    `open_screen kitchen` on its own line happens too.

    Returns the text with those calls removed, and the calls as (name, args).
    Only names of tools that were actually offered are recognised, so ordinary
    prose is never mistaken for a call.
    """
    by_name = {t["name"]: t for t in tools}
    if not text or not any(name in text for name in by_name):
        return text, []
    names = "|".join(re.escape(n) for n in by_name)
    calls: list[tuple[str, dict]] = []

    def braces(m: re.Match) -> str:
        body = m.group(2).replace('<|"|>', '"')
        body = re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', "{" + body + "}")
        try:
            args = json.loads(body)
        except ValueError:
            return m.group(0)                      # not a call we can read: leave the text alone
        calls.append((m.group(1), args if isinstance(args, dict) else {}))
        return ""

    def bare(m: re.Match) -> str:
        name, value = m.group(1), m.group(2)
        required = by_name[name].get("input_schema", {}).get("required") or []
        if len(required) != 1:
            return m.group(0)
        calls.append((name, {required[0]: value}))
        return ""

    out = re.sub(r'(?:<\|tool_call>)?\s*(?:call:)?\b(' + names + r')\s*\{(.*?)\}\s*(?:<tool_call\|>)?',
                 braces, text, flags=re.S)
    out = re.sub(r'^[ \t]*(' + names + r')[ \t]+([A-Za-z_]+)[ \t]*$', bare, out, flags=re.M)
    return out.strip(), calls


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
        # Structured output is not chat. 0.7/0.8 asks the model to be creative
        # about where the commas go, which is how "minutes": 12 " happens.
        self.json_temperature = float(getattr(s, "llm_json_temperature", 0.2) or 0.0)
        self.json_retry = bool(getattr(s, "llm_json_retry", True))
        self.json_tokens = int(getattr(s, "llm_json_tokens", 3000) or 0)
        self.timeout = float(getattr(s, "llm_timeout", 600.0) or 600.0)
        # Which kind of response_format this server turned out to accept:
        # "schema" (grammar-constrained — invalid JSON becomes impossible),
        # "object" (valid JSON, unconstrained shape), or "none".
        self.json_format = "schema" if getattr(s, "llm_json_schema", True) else "object"
        # The context window the model was LOADED with — not the one its card
        # advertises. Llama 3.2 1B is a 128K model that LM Studio will happily
        # load at 4096, and it is the 4096 that decides whether a request is
        # possible. 0 means "not told", in which case the arithmetic below is
        # skipped and the server gets to be the one that says no.
        self._settings = s               # the window itself: see the `context` property
        self.reserve = float(getattr(s, "llm_reserve", 0.35) or 0.35)
        # Tokens per second, as measured on this machine rather than as hoped.
        # Nothing in this file knew how fast the model was, and that is how a
        # token budget and a timeout came to be set to two numbers that cannot
        # both be satisfied: 10000 tokens at the 15/s this Mac actually manages
        # is 658 seconds of writing, against a 600-second limit. The request
        # could not have succeeded, and the only thing said about it was
        # "APITimeoutError: Request timed out".
        self.rate = 0.0

    @property
    def context(self) -> int:
        """LLM_CONTEXT, or what the server reported it loaded (app/context.py)."""
        from .. import context as window
        return window.current(self._settings)

    def _note_rate(self, resp, secs: float) -> None:
        """Remember how fast that was. Smoothed, so one slow first load — the
        model coming off disk — doesn't set the expectation for the session."""
        try:
            out = int(getattr(getattr(resp, "usage", None), "completion_tokens", 0) or 0)
        except Exception:  # noqa: BLE001
            return
        if out < 24 or secs <= 0:
            return                      # too short to measure anything by
        seen = out / secs
        self.rate = seen if not self.rate else (self.rate * 0.6 + seen * 0.4)

    def _budget_for(self, want: int, prompt_chars: int = 0) -> int:
        """The most tokens that can actually arrive.

        TWO ceilings, and for a long time this only knew about one of them.

        The CLOCK: a budget is a promise about the worst case, so if the model
        uses all of it the wait is budget / rate seconds. When that exceeds the
        timeout the request is arithmetically impossible, and raising the budget
        — the obvious response to a truncated recipe — makes it worse.

        The CONTEXT WINDOW: prompt and answer share it. Ask a model loaded at
        4096 tokens for 3000 tokens of recipe after a 3000-token prompt and no
        setting on earth can satisfy it; the server rejects the request before
        generating a thing. This is the failure that reads as

            The number of tokens to keep from the initial prompt is greater
            than the context length

        and it is far better caught here, where we can say which number to
        change, than reported by a server that doesn't know what the prompt
        was for.
        """
        if self.context and prompt_chars:
            prompt_tokens = int(prompt_chars / CHARS_PER_TOKEN) + 16
            room = self.context - prompt_tokens - 48       # 48 for the template
            if room < 240:
                raise LLMError(
                    f"this won't fit. The prompt is about {prompt_tokens} tokens and the "
                    f"model is loaded with a {self.context}-token context, which leaves "
                    f"{max(0, room)} for the answer — a recipe needs roughly 700. Load the "
                    "model with a bigger context (LM Studio: the Context Length slider; "
                    "Ollama: LLM_NUM_CTX in backend/.env), or lower LLM_IDS_CHARS so fewer "
                    "ingredient ids are sent.")
            if want > room:
                print(f"llm: asking for {room} tokens rather than {want} — the prompt is "
                      f"~{prompt_tokens} of this model's {self.context}-token context.")
                want = room
        if not self.rate or want <= 0:
            return want
        fits = int(self.rate * self.timeout * 0.9)
        if want <= fits:
            return want
        # Never so small that no recipe could fit; a floor that is honest about
        # being one, rather than a number printed and then quietly overruled.
        use = max(fits, 600)
        print(f"llm: {want} tokens at the {self.rate:.1f}/s this machine manages is "
              f"{want / self.rate:.0f}s, past the {self.timeout:.0f}s limit — asking for "
              f"{use} instead."
              + ("" if use == fits else
                 f" Even {use} needs {use / self.rate:.0f}s here, so raise LLM_TIMEOUT.")
              + (" Raise LLM_TIMEOUT to use the full budget." if use == fits else ""))
        return use

    def _timeout_words(self, want: int, secs: float) -> str:
        """What "Request timed out" should have said."""
        head = (f"the model was still writing after {secs / 60:.0f} minute"
                f"{'' if secs < 90 else 's'}, so the request was dropped")
        if self.rate and want:
            need = want / self.rate
            return (f"{head}. It writes about {self.rate:.0f} tokens a second here, and it "
                    f"was allowed up to {want} — {need / 60:.0f} minutes if it used them all, "
                    f"against a {self.timeout / 60:.0f}-minute limit. Lower LLM_JSON_TOKENS "
                    f"in backend/.env (a recipe needs roughly 800), or raise LLM_TIMEOUT.")
        return (f"{head}, and it had not produced enough to measure its speed. Either the "
                "model is still loading into memory, or it is reasoning before it answers "
                "— see docs/MODEL.md.")

    def _too_long_words(self, want: int, said: str) -> str:
        """What the server's arithmetic complaint should have said.

        The server knows its own numbers and nothing about what the request was
        for, so its message can only ever be "too long". These are the three
        things that actually shorten it, in the order worth trying them.
        """
        return (
            "the prompt plus the room reserved for the answer is larger than the context "
            f"window this model was loaded with. The server said: {said[:200]}\n"
            "Three things shorten it, best first:\n"
            "  1. Load the model with a bigger context. In LM Studio that is the Context "
            "Length slider on the model (4096 is the default and is not enough here; 8192 "
            "is). With Ollama, set LLM_NUM_CTX=8192 in backend/.env.\n"
            "  2. Send fewer ingredient ids: LLM_IDS_CHARS=2500 in backend/.env trims the "
            "catalogue to what is in the kitchen plus the seasonings. Nothing is lost — "
            "ingredients with no id go into the recipe as plain text.\n"
            f"  3. Ask for a shorter answer: LLM_JSON_TOKENS (currently allowing {want}). "
            "A recipe needs about 700.\n"
            "Setting LLM_CONTEXT to the window you loaded lets this be caught before the "
            "request is sent, with the numbers in it.")

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
        # `json()` manages its own response_format, stepping json_schema down to
        # json_object down to nothing and REMEMBERING which the server took. If
        # this function quietly dropped the field instead, the 400 would be
        # absorbed here, nothing would be learnt from it, and every later
        # request would spend a round trip rediscovering the same refusal.
        own_format = kw.pop("_own_format", False)

        plans: list[tuple[list, dict]] = []
        layers = list(self.extra_layers)
        while layers:
            merged: dict = {}
            for _, part in layers:
                merged.update(part)
            plans.append((list(layers), dict(kw, extra_body=merged)))
            layers = layers[1:]               # give up the most expendable one
        plans.append(([], dict(kw)))
        if kw.get("response_format") and not own_format:
            plans.append(([], {k: v for k, v in kw.items() if k != "response_format"}))

        want = int(kw.get("max_tokens") or 0)
        for i, (keep, body) in enumerate(plans):
            started = time.monotonic()
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model, temperature=body.pop("_temperature", self.temperature),
                    top_p=body.pop("_top_p", self.top_p), **body)
                self.extra_layers = keep      # what this server actually accepts
                self._note_rate(resp, time.monotonic() - started)
                return resp
            except Exception as e:  # noqa: BLE001
                # A timeout is not the server objecting to the request shape, so
                # there is nothing to drop and retry — but it IS the failure
                # people actually hit, and "APITimeoutError: Request timed out"
                # tells them nothing they can act on. Say it in seconds.
                if _is_timeout(e):
                    raise LLMError(self._timeout_words(want, time.monotonic() - started)) from None

                # "It won't fit" is not "I don't accept that field". Dropping
                # keep_alive and asking again cannot shorten a prompt, so the
                # generic handler below would just fail three more times while
                # printing lines about the thinking switch that had nothing to
                # do with it.
                if getattr(e, "status_code", None) == 400 and TOO_LONG_RX.search(str(e)):
                    raise LLMError(self._too_long_words(want, str(e))) from None

                if getattr(e, "status_code", None) != 400 or i == len(plans) - 1:
                    raise
                # When the caller owns the response_format, a 400 is far more
                # likely to be about THAT than about keep_alive. Hand it back
                # instead of walking down the layer plans printing "dropping
                # keep_alive" three times for a refusal that had nothing to do
                # with it; `json()` steps the format down and calls again with
                # every layer still intact.
                if own_format:
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
            content, written = inline_tool_calls(content, tools)
            if content:
                yield {"type": "delta", "text": content}
            if written and not calls:
                # Act on the calls the model wrote out, and don't go round again:
                # it has already said its sentence, and another round is another
                # full read of the prompt for a "Done." nobody needs.
                for name, args in written:
                    action, _ack = normalize(name, args, known)
                    if action is not None:
                        yield {"type": "action", "action": action}
                return
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

    # ------------------------------------------------------------------ json

    def _format_for(self, schema: dict):
        """The strongest response_format this server has not yet refused.

        `json_schema` is the one worth having: the server compiles the schema
        into a grammar and only samples tokens that keep the JSON valid, which
        removes the entire class of failure this file's docstring describes.
        Ollama, llama.cpp and LM Studio all support it. Anything that doesn't
        answers 400 and gets downgraded once, for the life of the process.
        """
        if self.json_format == "schema":
            return {"type": "json_schema",
                    "json_schema": {"name": "answer", "schema": schema}}
        if self.json_format == "object":
            return {"type": "json_object"}
        return None

    def _downgrade_format(self, why: str) -> bool:
        order = ["schema", "object", "none"]
        try:
            nxt = order[order.index(self.json_format) + 1]
        except (ValueError, IndexError):
            return False
        print(f"llm: {self.name} wouldn't take response_format "
              f"{self.json_format!r} ({why[:160]}) — using {nxt!r} from now on."
              + (" Constrained decoding is the thing that makes invalid JSON "
                 "impossible, so this is a real loss; a newer Ollama or "
                 "llama.cpp gets it back." if self.json_format == "schema" else ""))
        self.json_format = nxt
        return True

    async def json(self, system: str, user: str, schema: dict, max_tokens: int = 2400) -> dict:
        # A floor from the settings, then a ceiling from the clock. The floor
        # alone was half an answer: it let a budget be raised to a number this
        # machine cannot deliver inside the timeout, which turns a recipe that
        # was merely cut short into one that never arrives at all.
        # Everything that will be sent, including the schema `_one_json` pastes
        # onto the system prompt — leaving it out is how you conclude there is
        # room for an answer when there isn't.
        prompt_chars = len(system) + len(user) + len(json.dumps(schema)) + 120
        budget = self._budget_for(max(max_tokens, self.json_tokens), prompt_chars)

        attempts = 2 if self.json_retry else 1
        last: LLMError | None = None
        for attempt in range(attempts):
            # The retry is colder and blunter. If temperature 0.2 produced a
            # stray quote, 0.7 will not fix it and 0.0 might.
            temperature = self.json_temperature if attempt == 0 else 0.0
            nudge = "" if attempt == 0 else (
                "\n\nYour previous reply was not valid JSON. Return ONE JSON object "
                "and nothing else: no prose, no code fence, no trailing text. Check "
                "every quote, comma and brace before you finish.")
            try:
                return await self._one_json(system + nudge, user, schema, budget, temperature)
            except LLMError as e:
                last = e
                # Only a malformed reply is worth another go. A truncation, a
                # timeout or a model that reasoned instead of answering will do
                # exactly the same thing again, and on a local model that is
                # another minute of the user's evening for nothing.
                if "isn't valid JSON" not in str(e) or attempt + 1 >= attempts:
                    raise
                print(f"llm: invalid JSON from {self.name} — one more go at "
                      f"temperature 0. It said: {str(e)[:160]}")
        raise last or LLMError("the model didn't return structured output")

    async def _one_json(self, system: str, user: str, schema: dict,
                        budget: int, temperature: float) -> dict:
        while True:
            body = {
                "max_tokens": budget,
                "messages": [
                    {"role": "system", "content": system + "\n\nReply with one JSON object matching this "
                     "JSON Schema, and nothing else:\n" + json.dumps(schema)},
                    {"role": "user", "content": user},
                ],
                "_temperature": temperature,
                "_top_p": 1.0,
            }
            fmt = self._format_for(schema)
            if fmt:
                body["response_format"] = fmt
                body["_own_format"] = True    # a refusal comes back here, not silently dropped
            try:
                resp = await self._create(**body)
                break
            except Exception as e:  # noqa: BLE001
                # _create already drops response_format entirely as its last
                # resort, so reaching here with a 400 means the whole request
                # shape was refused. Step the format down and try once more —
                # json_schema is the field most likely to be the problem on an
                # older server.
                if getattr(e, "status_code", None) == 400 and self._downgrade_format(str(e)):
                    continue
                raise

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
                f"the model hit its {budget}-token limit and "
                + ("never reached the JSON" if "{" not in text else "stopped in the middle of it")
                + (" — it spent the budget reasoning first. Turn reasoning off in the "
                   "model server's own settings; see docs/MODEL.md."
                   if thinking else ". Try again, or ask for something simpler."))

        return parse_json(text, finish)


def make_llm(s: Settings):
    if s.llm_provider == "anthropic" and s.anthropic_api_key:
        return AnthropicLLM(s)
    if s.llm_provider == "openai" and (s.openai_api_key or s.openai_base_url):
        return OpenAICompatLLM(s)
    return None