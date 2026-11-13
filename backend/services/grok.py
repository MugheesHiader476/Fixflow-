"""Bounded chat adapters (xAI Responses, Groq Chat Completions); local function tools only."""

import asyncio
import json
from dataclasses import dataclass
from typing import cast

import httpx

from backend.connectors.core import record, records, string
from backend.processing.passages import tokens
from backend.schemas.agent import GrokConfig
from backend.security.policy import validate_disclosure
from backend.security.runtime import configured_secrets

MAX_RESPONSE_BYTES = 512 * 1024
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


@dataclass(frozen=True)
class GrokStep:
    calls: list[dict[str, object]]
    output: list[dict[str, object]]
    input_tokens: int | None
    output_tokens: int | None


async def respond(messages: list[dict[str, object]], tools: list[dict[str, object]], config: GrokConfig) -> GrokStep:
    if not config.configured or config.api_key is None:
        raise ValueError("Configure the chat API key and model")
    validate_disclosure(messages, configured_secrets())
    if tokens(json.dumps([messages, tools], ensure_ascii=False)) > config.input_token_limit:
        raise ValueError("Workflow context budget exceeded; narrow the request or selected sources")
    if config.provider == "groq":
        output, usage = await groq_output(messages, tools, config)
    else:
        output, usage = await xai_output(messages, tools, config)
    return validated_step(output, usage)


async def bounded_body(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Chat response exceeds bounds")
    return bytes(body)


async def xai_output(
    messages: list[dict[str, object]], tools: list[dict[str, object]], config: GrokConfig
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if config.api_key is None:
        raise ValueError("Configure the chat API key and model")
    async with (
        asyncio.timeout(config.timeout_seconds),
        httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False, follow_redirects=False) as client,
        client.stream(
            "POST",
            "https://api.x.ai/v1/responses",
            headers={"Authorization": "Bearer " + config.api_key.get_secret_value()},
            json={
                "model": config.model,
                "input": messages,
                "tools": tools,
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "store": False,
                "max_output_tokens": config.max_output_tokens,
                "include": ["reasoning.encrypted_content"],
                **({"reasoning": {"effort": config.reasoning_effort}} if config.reasoning_effort else {}),
            },
        ) as response,
    ):
        response.raise_for_status()
        body = await bounded_body(response)
    payload = record(json.loads(body))
    if payload.get("status") != "completed":
        raise ValueError("Chat response was not completed")
    return records(payload.get("output", [])), record(payload.get("usage") or {})


def inline_refs(schema: object, definitions: dict[str, object] | None = None, depth: int = 0) -> object:
    """Chat Completions tool parameters must be self-contained JSON Schema."""
    if depth > 32:
        raise ValueError("Tool schema nesting exceeds bounds")
    if isinstance(schema, dict):
        definitions = {**(definitions or {}), **record(schema.get("$defs", {}))}
        reference = schema.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            return inline_refs(definitions[reference.removeprefix("#/$defs/")], definitions, depth + 1)
        return {key: inline_refs(value, definitions, depth + 1) for key, value in schema.items() if key != "$defs"}
    if isinstance(schema, list):
        return [inline_refs(item, definitions, depth + 1) for item in schema]
    return schema


def chat_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": inline_refs(tool["parameters"]),
            },
        }
        for tool in tools
    ]


def chat_messages(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Convert the stored provider-neutral (Responses-style) history to Chat Completions messages."""
    output: list[dict[str, object]] = []
    for item in items:
        kind = item.get("type")
        if kind is None and item.get("role") in {"system", "user", "assistant"}:
            output.append({"role": item["role"], "content": string(item["content"])})
        elif kind == "message":
            parts = records(item.get("content", []))
            text = "".join(string(part.get("text", "")) for part in parts if part.get("type") == "output_text")
            output.append({"role": "assistant", "content": text})
        elif kind == "function_call":
            call = {
                "id": string(item["call_id"]),
                "type": "function",
                "function": {"name": string(item["name"]), "arguments": string(item["arguments"])},
            }
            previous = output[-1] if output else None
            if previous and previous.get("role") == "assistant" and "tool_calls" not in previous:
                previous["tool_calls"] = [call]
            else:
                output.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            output.append(
                {"role": "tool", "tool_call_id": string(item["call_id"]), "content": string(item["output"])}
            )
        elif kind != "reasoning":
            raise ValueError("Unsupported stored conversation item")
    return output


async def groq_output(
    messages: list[dict[str, object]], tools: list[dict[str, object]], config: GrokConfig
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if config.api_key is None:
        raise ValueError("Configure the chat API key and model")
    model = str(config.model)
    request: dict[str, object] = {
        "model": model,
        "messages": chat_messages(messages),
        "tools": chat_tools(tools),
        "tool_choice": "required",
        "max_completion_tokens": config.max_output_tokens,
        "temperature": 0.2,
    }
    if model.startswith("openai/gpt-oss"):
        # gpt-oss supports reasoning effort but not the parallel_tool_calls switch.
        if config.reasoning_effort:
            request["reasoning_effort"] = config.reasoning_effort
        request["include_reasoning"] = False
    else:
        request["parallel_tool_calls"] = False
    # One bounded retry when Groq rejects a malformed generated tool call (HTTP 400 tool_use_failed).
    body = b""
    for attempt in range(2):
        async with (
            asyncio.timeout(config.timeout_seconds),
            httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False, follow_redirects=False) as client,
            client.stream(
                "POST", GROQ_URL, headers={"Authorization": "Bearer " + config.api_key.get_secret_value()}, json=request
            ) as response,
        ):
            body = await bounded_body(response)
            if response.status_code == 400 and attempt == 0 and tool_use_failed(body):
                continue
            response.raise_for_status()
        break
    payload = record(json.loads(body))
    choices = records(payload.get("choices", []))
    if len(choices) != 1:
        raise ValueError("Chat response was not completed")
    message = record(choices[0].get("message", {}))
    output: list[dict[str, object]] = []
    if isinstance(message.get("content"), str) and message["content"]:
        output.append(
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": message["content"]}]}
        )
    for call in records(message.get("tool_calls") or []):
        function = record(call.get("function", {}))
        output.append(
            {
                "type": "function_call",
                "name": function.get("name"),
                "call_id": call.get("id"),
                "arguments": function.get("arguments"),
            }
        )
    usage = record(payload.get("usage") or {})
    return output, usage


def tool_use_failed(body: bytes) -> bool:
    try:
        error = record(record(json.loads(body)).get("error", {}))
    except (ValueError, TypeError):
        return False
    return error.get("code") == "tool_use_failed"


def validated_step(output: list[dict[str, object]], usage: dict[str, object]) -> GrokStep:
    calls: list[dict[str, object]] = []
    for item in output:
        if item.get("type") not in {"function_call", "reasoning", "message"}:
            raise ValueError("Unexpected provider-side tool output")
        if item.get("type") == "function_call":
            name, call_id, arguments = string(item["name"]), string(item["call_id"]), string(item["arguments"])
            if not name or not call_id or len(call_id) > 200 or len(arguments.encode()) > 48 * 1024:
                raise ValueError("Invalid chat tool call")
            calls.append({"type": "function_call", "name": name, "call_id": call_id, "arguments": arguments})
    if len(calls) != 1:
        raise ValueError("Expected one bounded local function call, including finish for the final answer")
    validate_disclosure(calls, configured_secrets())
    counts = [
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
    ]
    for count in counts:
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("Invalid chat usage")
    return GrokStep(calls, output, cast(int | None, counts[0]), cast(int | None, counts[1]))
