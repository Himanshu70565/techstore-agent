"""Gemini adapter with strict JSON parsing and no secret logging."""

from __future__ import annotations

import json
import os
from typing import Any

from google import genai

from logger import TerminalTrace
from models import Goal
from prompts import GOAL_PROMPT, decision_prompt

DEFAULT_MODEL = "gemini-3.1-flash-lite"


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Gemini response must be a JSON object")
    return value


class GeminiReasoner:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        trace: TerminalTrace | None = None,
    ) -> None:
        if client is None and not os.getenv("GOOGLE_GEMINI_KEY"):
            raise RuntimeError("GOOGLE_GEMINI_KEY is not configured")
        self.model = model
        self.client = client or genai.Client()
        self.trace = trace
        self._request_context: dict[str, Any] = {}

    def _generate(self, prompt: str) -> dict[str, Any]:
        if self.trace is not None:
            self.trace.gemini_requests += 1
            self.trace.section(
                "GEMINI REQUEST",
                self.trace.gemini_requests,
                model=self.model,
                request_context=self._request_context,
                system_and_prompt=prompt,
                sdk_config={"response_mime_type": "application/json"},
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
        except Exception as error:
            if self.trace is not None:
                self.trace.error(f"Gemini API error: {type(error).__name__}: {error}")
            raise
        text = getattr(response, "text", None)
        if self.trace is not None:
            self.trace.section(
                "GEMINI RESPONSE",
                self.trace.gemini_requests,
                raw_response=text if text is not None else "<empty>",
            )
        if not text:
            if self.trace is not None:
                self.trace.error("Gemini returned an empty response")
            raise RuntimeError("Gemini returned an empty response")
        try:
            parsed = parse_json_response(text)
            if self.trace is not None:
                self.trace.section(
                    "PARSED DECISION",
                    self.trace.gemini_requests,
                    parsed=parsed,
                    action=parsed.get("action"),
                    product_id=parsed.get("product_id"),
                    reason=parsed.get("reason"),
                )
            return parsed
        except (json.JSONDecodeError, ValueError) as error:
            if self.trace is not None:
                self.trace.error(f"Gemini response parsing error: {type(error).__name__}: {error}")
            raise RuntimeError("Gemini returned malformed JSON") from error

    def parse_goal(self, raw_goal: str, trace_context: dict[str, Any] | None = None) -> Goal:
        self._request_context = trace_context or {"operation": "parse_goal"}
        data = self._generate(GOAL_PROMPT.format(goal=raw_goal))
        categories = data.get("categories")
        if not isinstance(categories, list) or not all(isinstance(item, str) and item.strip() for item in categories):
            raise RuntimeError("Gemini returned an invalid goal category list")
        maximum = data.get("maximum_price")
        if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, (int, float))):
            raise RuntimeError("Gemini returned an invalid maximum price")
        attributes = data.get("required_attributes", [])
        if not isinstance(attributes, list) or not all(isinstance(item, str) for item in attributes):
            raise RuntimeError("Gemini returned invalid required attributes")
        return Goal(
            raw=raw_goal,
            categories=tuple(categories),
            objective=data.get("objective", "minimise_price"),
            maximum_price=float(maximum) if maximum is not None else None,
            required_attributes=tuple(attributes),
            requested_action=data.get("requested_action", "add_to_cart"),
        )

    def decide(
        self,
        goal: Goal,
        state: str,
        budget: int,
        scenario_prompt: str,
        trace_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._request_context = trace_context or {}
        return self._generate(
            decision_prompt(goal.raw, state, [
                "inspect_page", "search", "open_product", "compare_products",
                "add_to_cart", "verify_cart", "finish",
            ], budget, scenario_prompt)
        )
