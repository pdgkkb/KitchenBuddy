"""Language model, behind two methods.

    stream(system, messages)  -> async iterator of text   (the chat)
    json(system, user, schema) -> dict                     (recipes, filters)

Two implementations. Anthropic is the default. "openai" speaks the
OpenAI chat protocol, which also covers a model running on this machine
through Ollama — the fully offline option the product was built around."""

import json
import re
from collections.abc import AsyncIterator

from ..config import Settings


class LLMError(RuntimeError):
    pass


def tidy(messages: list[dict]) -> list[dict]:
    """Alternating user/assistant, starting with the user. Both APIs want
    that, and a chat UI doesn't naturally guarantee it."""
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

    async def json(self, system: str, user: str, schema: dict, max_tokens: int = 2400) -> dict:
        # Forcing a tool call gets schema-shaped output far more reliably
        # than asking for "JSON only" in prose.
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
                                  base_url=s.openai_base_url or None)
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
