"""OpenAI-compatible streaming adapter.

Targets the `/v1/chat/completions` endpoint with `stream=True`. Compatible with
OpenAI itself, and with any server speaking the same wire format (vLLM, llama.cpp
server, OpenRouter, Groq, …).

Translation rules:
  - Our `Message` content blocks → OpenAI `messages[*].content` parts.
    * TextBlock        → {"type": "text", "text": …}
    * ImageBlock       → {"type": "image_url", "image_url": {"url": "data:…"}}
    * ToolUseBlock     → assistant message `tool_calls` entry (one per block)
    * ToolResultBlock  → a separate {"role": "tool", …} message
  - Tool names: dots aren't allowed in OpenAI function names — we replace `.` with
    `__` on the way out and reverse it on the way in.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .types import (
    ImageBlock,
    Message,
    StreamEvent,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)

_NAME_SEP_OUT = "__"


def _encode_tool_name(name: str) -> str:
    return name.replace(".", _NAME_SEP_OUT)


def _decode_tool_name(name: str) -> str:
    return name.replace(_NAME_SEP_OUT, ".")


def _content_to_openai(blocks: list) -> list[dict] | str:
    """Translate a list of content blocks to OpenAI `content` parts."""
    parts: list[dict] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            parts.append({"type": "text", "text": b.text})
        elif isinstance(b, ImageBlock):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{b.mime};base64,{b.data}"},
                }
            )
        # ToolUseBlock / ToolResultBlock are handled at the message level.
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]  # OpenAI accepts a plain string
    return parts


def _messages_to_openai(messages: list[Message], system: str | None) -> list[dict]:
    """Flatten our Message list into OpenAI's wire format."""
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for m in messages:
        if m.role == "system":
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            out.append({"role": "system", "content": text})
            continue

        if m.role == "user":
            # tool_result blocks become standalone tool messages first
            tool_results = [b for b in m.content if isinstance(b, ToolResultBlock)]
            for tr in tool_results:
                # OpenAI tool messages take a string content
                text_parts: list[str] = []
                for c in tr.content:
                    if isinstance(c, TextBlock):
                        text_parts.append(c.text)
                    elif isinstance(c, ImageBlock):
                        text_parts.append("[image omitted]")
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": "\n".join(text_parts) or "(empty)",
                    }
                )
            other = [
                b for b in m.content if not isinstance(b, ToolResultBlock)
            ]
            if other:
                out.append({"role": "user", "content": _content_to_openai(other)})
            continue

        if m.role == "assistant":
            text_parts = [b for b in m.content if isinstance(b, TextBlock)]
            tool_uses = [b for b in m.content if isinstance(b, ToolUseBlock)]
            msg: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                msg["content"] = "".join(b.text for b in text_parts)
            else:
                msg["content"] = None
            if tool_uses:
                msg["tool_calls"] = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": _encode_tool_name(tu.name),
                            "arguments": json.dumps(tu.input, ensure_ascii=False),
                        },
                    }
                    for tu in tool_uses
                ]
            out.append(msg)

    return out


def _tools_to_openai(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": _encode_tool_name(t.name),
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


class OpenAICompatProvider:
    name: str
    model: str

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 600.0,
    ) -> None:
        self.name = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        # Granular timeout: keep ``connect``/``write``/``pool`` short so
        # genuine network-layer issues fail fast, but allow up to
        # ``timeout`` seconds of READ silence — long enough for slow
        # reasoning models, cold starts, and the inner LLM call that
        # the training kickoff fires from inside finalize_kickoff.
        self._timeout = httpx.Timeout(
            connect=10.0, read=timeout, write=60.0, pool=60.0
        )

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": _messages_to_openai(messages, system),
            "stream": True,
        }
        if tools:
            body["tools"] = _tools_to_openai(tools)
            body["tool_choice"] = "auto"
        if temperature is not None:
            body["temperature"] = temperature
        if output_schema is not None:
            # OpenAI's chat/completions structured-output shape — the model
            # is constrained to emit JSON conforming to the supplied schema.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": output_schema,
                    "strict": True,
                },
            }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=body,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")
                    yield StreamEvent(
                        type="error",
                        error=f"LLM provider {self.name} returned {resp.status_code}: {detail}",
                    )
                    return

                async for event in self._iter_sse(resp):
                    yield event

    async def _iter_sse(self, resp: httpx.Response) -> AsyncIterator[StreamEvent]:
        # Accumulators for the final assistant Message
        text_buf: list[str] = []
        # tool call streaming: openai sends partial chunks with index
        tool_calls: dict[int, dict[str, Any]] = {}

        async for raw_line in resp.aiter_lines():
            if not raw_line:
                continue
            if not raw_line.startswith("data:"):
                continue
            data = raw_line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            choices = payload.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}

            # Text token
            if (text_part := delta.get("content")) is not None:
                if isinstance(text_part, str) and text_part:
                    text_buf.append(text_part)
                    yield StreamEvent(type="text_delta", text=text_part)

            # Tool calls (streamed in pieces)
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

            finish_reason = choices[0].get("finish_reason")
            if finish_reason:
                # Build final tool_use blocks
                tool_use_blocks: list[ToolUseBlock] = []
                for idx in sorted(tool_calls.keys()):
                    slot = tool_calls[idx]
                    try:
                        args = json.loads(slot["arguments"]) if slot["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {"_raw": slot["arguments"]}
                    block = ToolUseBlock(
                        id=slot["id"] or f"call_{idx}",
                        name=_decode_tool_name(slot["name"]),
                        input=args,
                    )
                    tool_use_blocks.append(block)
                    yield StreamEvent(type="tool_use", tool_use=block)

                content: list = []
                if text_buf:
                    content.append(TextBlock(text="".join(text_buf)))
                content.extend(tool_use_blocks)
                yield StreamEvent(
                    type="message_done",
                    message=Message(role="assistant", content=content),
                )
                return
