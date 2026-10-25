"""Bounded xAI Responses adapter; local function tools only, no provider-side execution."""

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


@dataclass(frozen=True)
class GrokStep:
    calls: list[dict[str, object]]
    output: list[dict[str, object]]
    input_tokens: int | None
    output_tokens: int | None


async def respond(messages: list[dict[str, object]], tools: list[dict[str, object]], config: GrokConfig) -> GrokStep:
    if not config.configured or config.api_key is None:
        raise ValueError("Configure Grok API key and model")
    validate_disclosure(messages, configured_secrets())
    if tokens(json.dumps([messages, tools], ensure_ascii=False)) > config.input_token_limit:
        raise ValueError("Workflow context budget exceeded; narrow the request or selected sources")
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
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > 512 * 1024:
                raise ValueError("Grok response exceeds bounds")
    payload = record(json.loads(body))
    if payload.get("status") != "completed":
        raise ValueError("Grok response was not completed")
    calls: list[dict[str, object]] = []
    output = records(payload.get("output", []))
    for item in output:
        if item.get("type") not in {"function_call", "reasoning", "message"}:
            raise ValueError("Unexpected provider-side tool output")
        if item.get("type") == "function_call":
            name, call_id, arguments = string(item["name"]), string(item["call_id"]), string(item["arguments"])
            if not name or not call_id or len(call_id) > 200 or len(arguments.encode()) > 48 * 1024:
                raise ValueError("Invalid Grok tool call")
            calls.append({"type": "function_call", "name": name, "call_id": call_id, "arguments": arguments})
    if len(calls) != 1:
        raise ValueError("Expected one bounded local function call, including finish for the final answer")
    validate_disclosure(calls, configured_secrets())
    usage = record(payload.get("usage") or {})
    counts = [
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
    ]
    for count in counts:
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("Invalid Grok usage")
    return GrokStep(calls, output, cast(int | None, counts[0]), cast(int | None, counts[1]))
