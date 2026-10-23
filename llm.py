"""Gemini adapter with strict JSON parsing and no secret logging."""

from __future__ import annotations

import json
import os
from typing import Any

from google import genai

from models import Goal
from prompts import GOAL_PROMPT, SYSTEM_INSTRUCTIONS, decision_prompt

DEFAULT_MODEL = "gemini-3.5-flash-lite"


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
    def __init__(self, model: str = DEFAULT_MODEL, client: Any | None = None) -> None:
        if client is None and not os.getenv("GOOGLE_GEMINI_KEY"):
            raise RuntimeError("GOOGLE_GEMINI_KEY is not configured")
        self.model = model
        self.client = client or genai.Client()

    def _generate(self, prompt: str) -> dict[str, Any]:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini returned an empty response")
        try:
            return parse_json_response(text)
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("Gemini returned malformed JSON") from error

    def parse_goal(self, raw_goal: str) -> Goal:
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

    def decide(self, goal: Goal, state: str, budget: int) -> dict[str, Any]:
        return self._generate(
            decision_prompt(goal.raw, state, [
                "inspect_page", "search", "open_product", "compare_products",
                "add_to_cart", "verify_cart", "finish",
            ], budget)
        )
