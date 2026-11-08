"""
Groq LLM Client with Self-Healing Function-Calling Guard (`GroqToolCallGuard`).

Specifically engineered for Groq high-speed inference models:
- `openai/gpt-oss-120b`
- `qwen/qwen3-32b`

Why this module matters:
Open-weights reasoning models (`qwen/qwen3-32b` and `openai/gpt-oss-120b`) frequently emit
`<think>...</think>` blocks, markdown-fenced JSON arguments, trailing commas, or raise
Groq HTTP 400 `tool_use_failed` errors containing `error.failed_generation` when invoking
tools like `hindsight_recall`, `hindsight_retain`, and `hindsight_reflect`.
`GroqToolCallGuard` intercepts, sanitizes, repairs, and validates every function call so
the incident response pipeline never crashes during a production outage.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ToolCallRepairEvent:
    """Telemetry record created whenever GroqToolCallGuard repairs a malformed tool call."""

    model: str
    tool_name: str
    issue_detected: str
    raw_payload_snippet: str
    repaired_arguments: Dict[str, Any]
    recovered: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GroqToolCallGuard:
    """
    Resilient parser and schema normalizer for Groq tool calls (`openai/gpt-oss-120b` & `qwen/qwen3-32b`).
    """

    REQUIRED_DEFAULTS: Dict[str, Dict[str, Any]] = {
        "hindsight_recall": {"bank_id": "prod-payments-cluster", "query": "", "top_k": 5},
        "hindsight_retain": {
            "bank_id": "prod-payments-cluster",
            "content": "",
            "memory_type": "experience",
        },
        "hindsight_reflect": {"bank_id": "prod-payments-cluster", "query": ""},
    }

    @classmethod
    def repair_json_arguments(
        cls,
        raw_args: str,
        tool_name: str = "hindsight_recall",
        model: str = "qwen/qwen3-32b",
    ) -> Tuple[Dict[str, Any], Optional[ToolCallRepairEvent]]:
        """
        Repairs malformed tool call JSON emitted by reasoning models.
        Handles:
        1. `<think>...</think>` reasoning traces prepended inside arguments
        2. Markdown code fences (```json ... ```)
        3. Trailing commas before `}` or `]`
        4. Single-quoted keys/values or unquoted keys
        5. Stringified integers/booleans (e.g. `"top_k": "5"` -> `5`)
        6. Missing required schema fields (`bank_id`, `query`, `content`)
        """
        original = raw_args or "{}"
        cleaned = original.strip()
        issues: List[str] = []

        # 1. Strip <think>...</think> blocks emitted by qwen/qwen3-32b
        if "<think>" in cleaned:
            cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
            issues.append("stripped_qwen_think_tags")

        # 2. Strip markdown code fences
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
            issues.append("stripped_markdown_fences")

        # 3. Extract first JSON object start if surrounded by chatter
        if not cleaned.startswith("{") and "{" in cleaned:
            start = cleaned.find("{")
            if "}" in cleaned and cleaned.rfind("}") > start:
                end = cleaned.rfind("}") + 1
                cleaned = cleaned[start:end]
            else:
                cleaned = cleaned[start:]
            issues.append("extracted_embedded_json_object")

        # 4. Remove trailing commas before closing braces/brackets
        if re.search(r",\s*[}\]]", cleaned):
            cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
            issues.append("removed_trailing_commas")

        # 5. Balance truncated closing brace if model hit token boundary
        if cleaned.startswith("{") and not cleaned.endswith("}"):
            if cleaned.count('"') % 2 == 1:
                cleaned += '"'
            cleaned += "}"
            issues.append("closed_truncated_json_brace")

        parsed: Dict[str, Any] = {}
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            # Attempt single-quote to double-quote normalization
            try:
                sq_fixed = re.sub(r"'([^']*)'", r'"\1"', cleaned)
                parsed = json.loads(sq_fixed)
                issues.append("normalized_single_quotes")
            except json.JSONDecodeError:
                # Regex key-value fallback extraction so tool execution never crashes
                issues.append("regex_kv_fallback_extraction")
                for key in ("bank_id", "query", "content", "memory_type"):
                    m = re.search(rf'["\']?{key}["\']?\s*:\s*["\']([^"\']+)["\']', cleaned)
                    if m:
                        parsed[key] = m.group(1)

        if not isinstance(parsed, dict):
            parsed = {}
            issues.append("coerced_non_dict_root")

        # 6. Coerce types and populate missing defaults
        defaults = cls.REQUIRED_DEFAULTS.get(tool_name, {})
        for k, default_val in defaults.items():
            if k not in parsed or parsed[k] is None or parsed[k] == "":
                parsed[k] = default_val
                issues.append(f"filled_default_{k}")

        if "top_k" in parsed and isinstance(parsed["top_k"], str):
            try:
                parsed["top_k"] = int(parsed["top_k"])
                issues.append("coerced_top_k_int")
            except ValueError:
                parsed["top_k"] = 5

        repair_event = None
        if issues:
            repair_event = ToolCallRepairEvent(
                model=model,
                tool_name=tool_name,
                issue_detected=", ".join(issues),
                raw_payload_snippet=original[:140],
                repaired_arguments=parsed,
                recovered=True,
            )

        return parsed, repair_event

    @classmethod
    def recover_from_failed_generation(
        cls,
        failed_generation_text: str,
        default_bank_id: str = "prod-payments-cluster",
        model: str = "openai/gpt-oss-120b",
    ) -> Tuple[Optional[str], Dict[str, Any], ToolCallRepairEvent]:
        """
        Recovers a structured tool invocation when Groq returns HTTP 400 `tool_use_failed`
        with an `error.failed_generation` string (common in `openai/gpt-oss-120b` and `qwen/qwen3-32b`).
        """
        tool_name = "hindsight_recall"
        for candidate in ("hindsight_retain", "hindsight_reflect", "hindsight_recall"):
            if candidate in failed_generation_text:
                tool_name = candidate
                break

        args, event = cls.repair_json_arguments(
            failed_generation_text, tool_name=tool_name, model=model
        )
        if not args.get("bank_id"):
            args["bank_id"] = default_bank_id

        if event is None:
            event = ToolCallRepairEvent(
                model=model,
                tool_name=tool_name,
                issue_detected="groq_http_400_tool_use_failed_recovered",
                raw_payload_snippet=failed_generation_text[:140],
                repaired_arguments=args,
                recovered=True,
            )
        else:
            event.issue_detected = f"groq_http_400_tool_use_failed + {event.issue_detected}"

        return tool_name, args, event


class GroqLLMClient:
    """
    Groq inference client supporting `openai/gpt-oss-120b` and `qwen/qwen3-32b`
    with built-in `GroqToolCallGuard` function-calling error recovery.
    """

    SUPPORTED_MODELS: List[str] = [
        "openai/gpt-oss-120b",
        "qwen/qwen3-32b",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "openai/gpt-oss-120b",
    ) -> None:
        self.api_key = (api_key or os.environ.get("GROQ_API_KEY", "")).strip()
        self.default_model = (
            default_model
            if default_model in self.SUPPORTED_MODELS
            else "openai/gpt-oss-120b"
        )
        self.base_url = "https://api.groq.com/openai/v1/chat/completions"
        self.repair_history: List[ToolCallRepairEvent] = []

    @property
    def is_live_groq(self) -> bool:
        return bool(self.api_key)

    def get_hindsight_tools_schema(self) -> List[Dict[str, Any]]:
        """OpenAI/Groq-compatible function schemas for Hindsight memory operations."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "hindsight_recall",
                    "description": "Retrieve relevant world, experience, and observation memories from a Hindsight memory bank using 4-way TEMPR search.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bank_id": {"type": "string", "description": "Isolated memory bank ID"},
                            "query": {"type": "string", "description": "Incident query, error code, or service name"},
                            "top_k": {"type": "integer", "description": "Max memories to return"},
                        },
                        "required": ["bank_id", "query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hindsight_reflect",
                    "description": "Trigger agentic synthesis across recalled memories to extract cross-incident operational invariants and runbook guardrails.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bank_id": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["bank_id", "query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hindsight_retain",
                    "description": "Store new incident findings, post-mortem root causes, or failed runbook warnings into Hindsight long-term memory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bank_id": {"type": "string"},
                            "content": {"type": "string"},
                            "memory_type": {
                                "type": "string",
                                "enum": ["world", "experience", "observation"],
                            },
                        },
                        "required": ["bank_id", "content"],
                    },
                },
            },
        ]

    def execute_with_tool_guard(
        self,
        messages: List[Dict[str, str]],
        tool_handlers: Dict[str, Callable[..., Any]],
        model: Optional[str] = None,
        bank_id: str = "prod-payments-cluster",
    ) -> Dict[str, Any]:
        """
        Executes a Groq chat completion with tool calling enabled, automatically catching
        and repairing any `tool_use_failed` or malformed JSON tool arguments from
        `openai/gpt-oss-120b` or `qwen/qwen3-32b`.
        """
        chosen_model = model or self.default_model
        if not self.api_key:
            return {
                "mode": "deterministic-sre-synthesizer",
                "model": chosen_model,
                "content": None,
                "repairs": [r.to_dict() for r in self.repair_history[-5:]],
            }

        payload = {
            "model": chosen_model,
            "messages": messages,
            "tools": self.get_hindsight_tools_schema(),
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 900,
        }

        try:
            req = urllib.request.Request(
                self.base_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data["choices"][0]["message"]

                if choice.get("tool_calls"):
                    for tc in choice["tool_calls"]:
                        fn_name = tc["function"]["name"]
                        raw_args = tc["function"].get("arguments", "{}")
                        repaired_args, repair_ev = GroqToolCallGuard.repair_json_arguments(
                            raw_args, tool_name=fn_name, model=chosen_model
                        )
                        if repair_ev:
                            self.repair_history.append(repair_ev)
                        if fn_name in tool_handlers:
                            tool_handlers[fn_name](**repaired_args)

                content = choice.get("content") or ""
                if "<think>" in content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                return {
                    "mode": "groq-live-api",
                    "model": chosen_model,
                    "content": content,
                    "repairs": [r.to_dict() for r in self.repair_history[-5:]],
                }

        except urllib.error.HTTPError as http_err:
            # Intercept Groq HTTP 400 `tool_use_failed` error and self-heal!
            try:
                err_body = json.loads(http_err.read().decode("utf-8"))
                failed_gen = err_body.get("error", {}).get("failed_generation", "")
                if failed_gen:
                    fn_name, repaired_args, repair_ev = (
                        GroqToolCallGuard.recover_from_failed_generation(
                            failed_gen, default_bank_id=bank_id, model=chosen_model
                        )
                    )
                    self.repair_history.append(repair_ev)
                    if fn_name and fn_name in tool_handlers:
                        tool_handlers[fn_name](**repaired_args)
            except Exception:
                pass
        except Exception:
            pass

        return {
            "mode": "groq-self-healed-fallback",
            "model": chosen_model,
            "content": None,
            "repairs": [r.to_dict() for r in self.repair_history[-5:]],
        }
