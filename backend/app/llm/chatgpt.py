"""ChatGPT subscription provider (OAuth + Responses API).

This adapter targets `https://chatgpt.com/backend-api/codex/responses` — the
endpoint used by the official Codex CLI / opencode. It speaks the OpenAI
**Responses API** (not chat/completions): the request body uses `input` (not
`messages`), tool calls are top-level `function_call` items, tool results are
`function_call_output` items, and the response payload uses `output[]` rather
than `choices[]`.

Authentication is via OAuth tokens managed by `chatgpt_auth` — the user runs
`second-brain chatgpt-login <provider>` once to populate the token file.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from . import chatgpt_auth
from .types import (
    ImageBlock,
    Message,
    StreamEvent,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"


def _truncate(s: str | None, n: int = 4000) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + f"…[+{len(s) - n} chars]"

# The Responses API enforces tool/function names matching ^[a-zA-Z0-9_-]+$.
# Our internal names use a `family.verb` shape with a dot, so encode the dot
# on the way out and reverse on the way in. Identical scheme to openai_compat.
_NAME_SEP_OUT = "__"


def _encode_tool_name(name: str) -> str:
    return name.replace(".", _NAME_SEP_OUT)


def _decode_tool_name(name: str) -> str:
    return name.replace(_NAME_SEP_OUT, ".")


# ── Wire-format conversion ───────────────────────────────────────────────────


def _content_to_input_parts(blocks: list) -> list[dict]:
    """Translate user content blocks to Responses API content parts."""
    parts: list[dict] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            parts.append({"type": "input_text", "text": b.text})
        elif isinstance(b, ImageBlock):
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{b.mime};base64,{b.data}",
                }
            )
    return parts


def _messages_to_input(messages: list[Message]) -> list[dict]:
    """Flatten our Message list into the Responses API `input` array."""
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            # System prompts are passed via the top-level `instructions` field.
            # If somehow a system Message slipped into history, fold it in.
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            out.append({"role": "system", "content": text})
            continue

        if m.role == "user":
            tool_results = [b for b in m.content if isinstance(b, ToolResultBlock)]
            for tr in tool_results:
                text_parts: list[str] = []
                for c in tr.content:
                    if isinstance(c, TextBlock):
                        text_parts.append(c.text)
                    elif isinstance(c, ImageBlock):
                        text_parts.append("[image omitted]")
                out.append(
                    {
                        "type": "function_call_output",
                        "call_id": tr.tool_use_id,
                        "output": "\n".join(text_parts) or "(empty)",
                    }
                )
            other = [b for b in m.content if not isinstance(b, ToolResultBlock)]
            parts = _content_to_input_parts(other)
            if parts:
                out.append({"role": "user", "content": parts})
            continue

        if m.role == "assistant":
            text_parts = [b for b in m.content if isinstance(b, TextBlock)]
            tool_uses = [b for b in m.content if isinstance(b, ToolUseBlock)]
            if text_parts:
                joined = "".join(b.text for b in text_parts)
                out.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": joined}],
                    }
                )
            for tu in tool_uses:
                out.append(
                    {
                        "type": "function_call",
                        "call_id": tu.id,
                        "name": _encode_tool_name(tu.name),
                        "arguments": json.dumps(tu.input, ensure_ascii=False),
                    }
                )

    return out


def _tools_to_responses(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "name": _encode_tool_name(t.name),
            "description": t.description,
            "parameters": _sanitise_json_schema(t.input_schema),
            "strict": False,
        }
        for t in tools
    ]


_ALLOWED_SCHEMA_KEYS = {
    "type", "properties", "required", "items", "enum", "description",
    "default", "anyOf", "oneOf", "allOf", "additionalProperties",
    "minimum", "maximum", "minItems", "maxItems", "pattern", "const", "nullable",
}


def _sanitise_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip non-standard JSON Schema keys the Responses API rejects."""
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k not in _ALLOWED_SCHEMA_KEYS:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {
                pk: _sanitise_json_schema(pv) if isinstance(pv, dict) else pv
                for pk, pv in v.items()
            }
        elif k == "items" and isinstance(v, dict):
            out[k] = _sanitise_json_schema(v)
        else:
            out[k] = v
    return out


# ── Provider ─────────────────────────────────────────────────────────────────


class ChatGPTProvider:
    """LLMProvider implementation for the ChatGPT subscription endpoint."""

    name: str
    model: str

    def __init__(self, *, name: str, model: str, timeout: float = 300.0) -> None:
        self.name = name
        self.model = model
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        access_token, account_id = chatgpt_auth.get_valid_access_token(self.name)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Accept": "text/event-stream",
            # The Codex backend looks for these versioning headers.
            "OpenAI-Beta": "responses=v1",
            "originator": "second-brain",
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id
        return headers

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
        try:
            headers = self._headers()
        except RuntimeError as exc:
            yield StreamEvent(type="error", error=str(exc))
            return

        payload: dict[str, Any] = {
            "model": model or self.model,
            "instructions": system or "You are a helpful assistant.",
            "input": _messages_to_input(messages),
            "store": False,
            "stream": True,
        }
        if tools:
            payload["tools"] = _tools_to_responses(tools)
        if output_schema is not None:
            # Responses API structured-output shape: `text.format` with
            # type=json_schema. The model is then constrained to emit
            # JSON matching the schema; the response still arrives as
            # text on the wire (we json.loads it).
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "structured_output",
                    "schema": _sanitise_json_schema(output_schema),
                    "strict": True,
                },
            }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", ENDPOINT, headers=headers, json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:2000]
                    yield StreamEvent(
                        type="error",
                        error=f"ChatGPT provider {self.name} returned "
                              f"{resp.status_code}: {body}",
                    )
                    return

                async for ev in self._consume_sse(resp):
                    yield ev

    async def _consume_sse(self, resp: httpx.Response) -> AsyncIterator[StreamEvent]:
        # Per-output_index accumulators so we can rebuild the final assistant
        # turn even if the server sends interleaved deltas across multiple
        # output items.
        text_per_output: dict[int, list[str]] = {}
        tool_calls: dict[int, dict[str, Any]] = {}
        ordered_indices: list[int] = []     # stable order for the final message
        current_event = ""
        completed_response: dict[str, Any] | None = None
        seen_event_types: set[str] = set()  # for diagnostic logging

        async for raw_line in resp.aiter_lines():
            if not raw_line:
                continue
            if raw_line.startswith(":"):
                continue
            if raw_line.startswith("event:"):
                current_event = raw_line[len("event:") :].strip()
                continue
            if not raw_line.startswith("data:"):
                continue

            data_str = raw_line[len("data:") :].strip()
            if not data_str:
                continue
            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            etype = current_event or payload.get("type", "")
            seen_event_types.add(etype)

            if etype == "response.output_item.added":
                item = payload.get("item") or {}
                idx = int(payload.get("output_index", 0))
                if idx not in ordered_indices:
                    ordered_indices.append(idx)
                if item.get("type") == "function_call":
                    tool_calls[idx] = {
                        "id": item.get("call_id") or item.get("id") or f"call_{idx}",
                        "name": _decode_tool_name(item.get("name", "")),
                        "arguments": item.get("arguments", "") or "",
                    }
                elif item.get("type") == "message":
                    text_per_output.setdefault(idx, [])

            elif etype == "response.output_text.delta":
                idx = int(payload.get("output_index", 0))
                if idx not in ordered_indices:
                    ordered_indices.append(idx)
                delta = payload.get("delta", "")
                if delta:
                    text_per_output.setdefault(idx, []).append(delta)
                    yield StreamEvent(type="text_delta", text=delta)

            elif etype == "response.output_text.done":
                # Some servers only emit `done` (with the full text) and not
                # `delta`. Capture the text if we haven't accumulated any yet.
                idx = int(payload.get("output_index", 0))
                if idx not in ordered_indices:
                    ordered_indices.append(idx)
                full = payload.get("text", "") or ""
                if full and not text_per_output.get(idx):
                    text_per_output[idx] = [full]
                    yield StreamEvent(type="text_delta", text=full)

            elif etype == "response.output_item.done":
                # Item finalization — extract text/function_call from the
                # carried `item` if we missed the deltas.
                item = payload.get("item") or {}
                idx = int(payload.get("output_index", 0))
                if idx not in ordered_indices:
                    ordered_indices.append(idx)
                if item.get("type") == "message":
                    if not text_per_output.get(idx):
                        text = "".join(
                            c.get("text", "") or ""
                            for c in (item.get("content") or [])
                            if c.get("type") == "output_text"
                        )
                        if text:
                            text_per_output[idx] = [text]
                            yield StreamEvent(type="text_delta", text=text)
                elif item.get("type") == "function_call":
                    tool_calls.setdefault(
                        idx,
                        {
                            "id": item.get("call_id") or item.get("id") or f"call_{idx}",
                            "name": _decode_tool_name(item.get("name", "")),
                            "arguments": item.get("arguments", "") or "",
                        },
                    )
                    if not tool_calls[idx]["arguments"] and item.get("arguments"):
                        tool_calls[idx]["arguments"] = item["arguments"]

            elif etype == "response.function_call_arguments.delta":
                idx = int(payload.get("output_index", 0))
                if idx not in ordered_indices:
                    ordered_indices.append(idx)
                slot = tool_calls.setdefault(
                    idx, {"id": f"call_{idx}", "name": "", "arguments": ""}
                )
                delta = payload.get("delta", "")
                if delta:
                    slot["arguments"] += delta

            elif etype == "response.completed":
                # Codex sometimes wraps under `response`, sometimes ships the
                # response object directly in the data payload.
                completed_response = payload.get("response") or payload

            elif etype == "error" or etype == "response.failed":
                err = payload.get("error") or payload.get("message") or "ChatGPT stream error"
                if isinstance(err, dict):
                    err = err.get("message") or json.dumps(err)
                yield StreamEvent(type="error", error=str(err))
                return

        # Build the final assistant Message. If the server sent a complete
        # response object, prefer that — it's authoritative.
        if completed_response is not None:
            final_blocks = self._blocks_from_completed(completed_response)
            if not final_blocks:
                # Fall back to streamed accumulators if the completed
                # response had an unexpected shape.
                final_blocks = self._blocks_from_streamed(
                    ordered_indices, text_per_output, tool_calls
                )
        else:
            final_blocks = self._blocks_from_streamed(
                ordered_indices, text_per_output, tool_calls
            )

        if not final_blocks:
            log.warning(
                "ChatGPT %s produced an empty assistant turn. "
                "events_seen=%s; completed_response_keys=%s; "
                "completed_output=%s; ordered=%s; text_buffers=%s; tool_calls=%s",
                self.name,
                sorted(seen_event_types),
                sorted((completed_response or {}).keys()) if completed_response else None,
                _truncate(json.dumps((completed_response or {}).get("output"), default=str)),
                ordered_indices,
                {k: "".join(v)[:200] for k, v in text_per_output.items()},
                {k: {**v, "arguments": str(v.get("arguments"))[:200]} for k, v in tool_calls.items()},
            )
            # Surface a clearer error to the orchestrator instead of a
            # silently-empty bubble.
            yield StreamEvent(
                type="error",
                error=(
                    f"ChatGPT provider {self.name} returned no usable content "
                    f"(events seen: {sorted(seen_event_types)}). "
                    f"Check the backend log for the full payload."
                ),
            )
            return

        # Emit a tool_use event for each function call (matches the OpenAI-compat
        # adapter's contract — orchestrator depends on these to dispatch tools).
        for b in final_blocks:
            if isinstance(b, ToolUseBlock):
                yield StreamEvent(type="tool_use", tool_use=b)

        yield StreamEvent(
            type="message_done",
            message=Message(role="assistant", content=final_blocks),
        )

    # ── Final-message builders ───────────────────────────────────────────

    @staticmethod
    def _blocks_from_completed(response: dict[str, Any]) -> list:
        blocks: list = []
        for item in response.get("output") or []:
            t = item.get("type")
            if t == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        blocks.append(TextBlock(text=c.get("text", "")))
            elif t == "function_call":
                raw_args = item.get("arguments", "{}") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
                blocks.append(
                    ToolUseBlock(
                        id=item.get("call_id") or item.get("id") or "call_unknown",
                        name=_decode_tool_name(item.get("name", "unknown")),
                        input=args,
                    )
                )
        return blocks

    @staticmethod
    def _blocks_from_streamed(
        ordered_indices: list[int],
        text_per_output: dict[int, list[str]],
        tool_calls: dict[int, dict[str, Any]],
    ) -> list:
        blocks: list = []
        for idx in ordered_indices:
            if idx in text_per_output and text_per_output[idx]:
                blocks.append(TextBlock(text="".join(text_per_output[idx])))
            elif idx in tool_calls:
                slot = tool_calls[idx]
                try:
                    args = json.loads(slot["arguments"]) if slot["arguments"] else {}
                except json.JSONDecodeError:
                    args = {"_raw": slot["arguments"]}
                blocks.append(
                    ToolUseBlock(id=slot["id"], name=slot["name"] or "unknown", input=args)
                )
        return blocks
