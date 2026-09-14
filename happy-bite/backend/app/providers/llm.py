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


def parse_json(text: str) -> dict:
    clean = re.sub(r"```(?:json)?", "", text).strip()
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end < 0:
        raise LLMError("No JSON in the reply")
    return json.loads(clean[start:end + 1])


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


class OpenAICompatLLM:
    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key or "none",
                      base_url=s.openai_base_url or None,
                      timeout=120.0)
        self.model = s.llm_model
        self.name = f"openai:{s.llm_model}" + (" (local)" if s.openai_base_url else "")

    async def stream(self, system: str, messages: list[dict], max_tokens: int = 900) -> AsyncIterator[str]:
        resp = await self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens, stream=True,
            messages=[{"role": "system", "content": system}, *tidy(messages)],
        )
        async for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

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
                completion = await self.client.chat.completions.create(
                    model=self.model, max_tokens=max_tokens, messages=work, tools=oa_tools,
                )
            except (TypeError, ValueError):
                completion = await self.client.chat.completions.create(
                    model=self.model, max_tokens=max_tokens, messages=work,
                )
            m = completion.choices[0].message
            content = m.content or ""
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
        resp = await self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system + "\n\nReply with one JSON object matching this "
                 "JSON Schema, and nothing else:\n" + json.dumps(schema)},
                {"role": "user", "content": user},
            ],
        )
        return parse_json(resp.choices[0].message.content or "")


def make_llm(s: Settings):
    if s.llm_provider == "anthropic" and s.anthropic_api_key:
        return AnthropicLLM(s)
    if s.llm_provider == "openai" and (s.openai_api_key or s.openai_base_url):
        return OpenAICompatLLM(s)
    return None
