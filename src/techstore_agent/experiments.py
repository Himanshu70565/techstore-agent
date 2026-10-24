"""Compatibility helpers for the prompt scenarios."""

from .prompts import PROMPT_KEYS, PROMPTS


def scenario_by_name(name: str) -> str:
    """Return the selected scenario prompt or raise a clear error."""
    if name not in PROMPTS:
        raise ValueError(
            f"Unknown scenario {name!r}. Choose one of: {', '.join(PROMPT_KEYS)}"
        )
    return PROMPTS[name]
