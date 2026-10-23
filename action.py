"""Validation and execution of the browser agent's small action vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from browser import BrowserAgent, PageState
from models import Goal

ALLOWED_ACTIONS = {
    "search",
    "inspect_page",
    "open_product",
    "compare_products",
    "add_to_cart",
    "verify_cart",
    "finish",
}
MAX_STRING_LENGTH = 200


@dataclass(frozen=True)
class ValidationResult:
    approved: bool
    message: str


def validate_action(
    decision: Any,
    goal: Goal | None = None,
    state: PageState | None = None,
    cart_verified: bool = False,
    catalogue: tuple[Any, ...] = (),
    inspected_product_ids: set[str] | None = None,
) -> tuple[bool, str]:
    """Validate an untrusted model decision before it reaches Playwright."""
    if not isinstance(decision, dict):
        return False, "Decision must be a dictionary"
    if set(decision) - {"action", "value", "product_id", "product_ids", "reason"}:
        return False, "Decision contains unknown fields"

    action = decision.get("action")
    if action not in ALLOWED_ACTIONS:
        return False, f"Action not allowed: {action}"

    value = decision.get("value")
    if value is not None and (
        not isinstance(value, str) or not value.strip() or len(value) > MAX_STRING_LENGTH
    ):
        return False, "value must be a non-empty string within the length limit"

    product_id = decision.get("product_id")
    if product_id is not None and (
        not isinstance(product_id, str) or not product_id.strip() or len(product_id) > MAX_STRING_LENGTH
    ):
        return False, "product_id must be a non-empty string within the length limit"

    product_ids = decision.get("product_ids")
    if product_ids is not None and (
        not isinstance(product_ids, list)
        or not product_ids
        or len(product_ids) > 3
        or any(not isinstance(item, str) or not item.strip() for item in product_ids)
    ):
        return False, "product_ids must contain one to three non-empty strings"

    requirements = {
        "search": value,
        "open_product": product_id,
        "add_to_cart": product_id,
        "compare_products": product_ids,
    }
    required = requirements.get(action)
    if required is None and action in requirements:
        return False, f"{action} requires its argument"

    if action == "add_to_cart" and goal is not None and goal.requested_action != "add_to_cart":
        return False, "Adding to cart is not part of the requested goal"

    if action == "open_product" and state is not None and product_id not in state.product_ids:
        return False, "Product is not present in the observed page state"

    if action == "add_to_cart" and state is not None and product_id not in state.product_ids:
        return False, "Product must be observed before it can be added"

    if action == "add_to_cart" and catalogue:
        catalogue_ids = {product.id for product in catalogue}
        if product_id not in catalogue_ids:
            return False, "Product must belong to the collected catalogue"
        if inspected_product_ids is not None and inspected_product_ids != catalogue_ids:
            return False, "All catalogue products must be inspected before adding to cart"

    if action == "finish" and goal is not None and goal.requested_action == "add_to_cart":
        if not cart_verified:
            return False, "Cannot finish a cart task before the cart has been verified"

    return True, "Action approved"


def execute_action(browser: BrowserAgent, decision: dict[str, Any]) -> str | PageState:
    """Execute only actions that have already passed validation."""
    action = decision["action"]
    if action == "search":
        browser.search(decision["value"])
        return "Search action executed"
    if action == "inspect_page":
        return browser.observe()
    if action == "open_product":
        browser.open_product(decision["product_id"])
        return "Product opened"
    if action == "compare_products":
        browser.compare_products(decision["product_ids"])
        return "Products compared"
    if action == "add_to_cart":
        browser.add_to_cart(decision["product_id"])
        return "Product added to cart"
    if action == "verify_cart":
        return browser.verify_cart()
    if action == "finish":
        return "Agent finished"
    raise ValueError(f"Unsupported action: {action}")
