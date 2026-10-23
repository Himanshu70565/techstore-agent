"""Command-line entry point for the bounded TechStore research prototype."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from action import execute_action, validate_action
from browser import BrowserAgent
from experiments import scenario_by_name
from llm import GeminiReasoner
from logger import ExperimentLogger
from models import Goal, PageState, Product


DEFAULT_PROFILE_DIR = "./browser_data"


def create_persistent_context(playwright: Any, profile_dir: str) -> Any:
    """Launch the visible Chromium context that owns the persistent profile."""
    profile_path = Path(profile_dir).expanduser()
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_path),
        headless=False,
    )


def eligible_products(products: tuple[Product, ...], goal: Goal) -> list[Product]:
    """Filter and sort only products supported by observed evidence."""
    terms = {term.lower() for category in goal.categories for term in category.split()}
    candidates = [
        product
        for product in products
        if (not terms or any(term in product.category.lower() for term in terms))
        and (goal.maximum_price is None or product.price <= goal.maximum_price)
        and all(
            attribute.lower() in f"{product.description} {product.specifications}".lower()
            for attribute in goal.required_attributes
        )
    ]
    return sorted(candidates, key=lambda product: (product.price, product.id))


def run(
    goal_text: str,
    scenario: str = "baseline",
    log_path: str = "runs.jsonl",
    max_actions: int = 12,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    keep_open: bool = True,
    input_func: Any = input,
) -> dict[str, Any]:
    logger = ExperimentLogger(log_path)
    reasoner = GeminiReasoner()
    started = time.monotonic()
    goal = reasoner.parse_goal(goal_text)
    print("Running scenario:", scenario, "with goal:", goal_text, "goal: ", goal)
    selected: Product | None = None
    cart_verified = False
    cart_verification: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = []

    logger.record(
        "run_started",
        scenario=scenario,
        condition=scenario_by_name(scenario).condition,
        goal=goal,
    )
    with sync_playwright() as playwright:
        context = create_persistent_context(playwright, profile_dir)
        page = context.pages[0] if context.pages else context.new_page()
        agent = BrowserAgent(page)
        try:
            agent.open()
            for _ in range(max_actions):
                state = agent.observe()
                candidates = eligible_products(state.products, goal)
                if candidates:
                    selected = candidates[0]
                decision = reasoner.decide(
                    goal,
                    json.dumps(
                        {
                            "url": state.url,
                            "products": [product.__dict__ for product in candidates],
                            "text": state.text,
                        },
                        ensure_ascii=True,
                    ),
                    max_actions - len(actions),
                )
                approved, message = validate_action(decision, goal, state)
                logger.record("decision", decision=decision, approved=approved, validation=message)
                if not approved:
                    raise RuntimeError(message)
                if decision["action"] == "finish":
                    if selected is None or not cart_verified:
                        raise RuntimeError("Cannot finish before selecting and verifying a cart item")
                    break
                result = execute_action(agent, decision)
                actions.append(decision)
                logger.record("action_executed", decision=decision, result=result)
                if decision["action"] == "add_to_cart":
                    verification = agent.verify_cart(decision["product_id"])
                    logger.record("cart_verification", verification=verification)
                    if not verification.verified:
                        raise RuntimeError(verification.message)
                    cart_verified = True
                    cart_verification = verification.__dict__
                    break
            else:
                raise RuntimeError("Maximum action count reached")
        finally:
            report = {
                "scenario": scenario,
                "goal": goal_text,
                "selected_product": selected.__dict__ if selected else None,
                "cart_verified": cart_verified,
                "cart_verification": cart_verification,
                "actions": actions,
                "duration_seconds": round(time.monotonic() - started, 3),
                "log_path": str(Path(log_path).expanduser()),
                "profile_dir": str(Path(profile_dir).expanduser()),
            }
            logger.record("run_finished", report=report)
            _print_completion_summary(report)
            if keep_open:
                input_func(
                    "\nThe browser is still open for manual inspection. "
                    "Press Enter to close it..."
                )
            context.close()
    return report


def _print_completion_summary(report: dict[str, Any]) -> None:
    """Print verified completion facts before the persistent context is closed."""
    product = report["selected_product"]
    print("\n--- Agent completion ---")
    if product:
        print(f"Selected product: {product['name']}")
        print(f"Selected price: NZ${product['price']:.2f}")
    else:
        print("Selected product: none")
        print("Selected price: unavailable")
    print(f"Cart verification: {report['cart_verified']}")
    if report["cart_verification"]:
        print(f"Cart result: {report['cart_verification']['message']}")
    print(f"Experiment log: {report['log_path']}")
    print(f"Browser profile: {report['profile_dir']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "goal",
        nargs="?",
        default="Find the cheapest headphones and add them to the cart.",
    )
    parser.add_argument("--scenario", default="baseline")
    parser.add_argument("--log", default="runs.jsonl")
    parser.add_argument("--max-actions", type=int, default=12)
    parser.add_argument(
        "--profile-dir",
        default=DEFAULT_PROFILE_DIR,
        help="Directory used for the persistent Chromium profile.",
    )
    parser.add_argument(
        "--keep-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep the visible browser open until Enter is pressed (default: enabled).",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.goal,
                args.scenario,
                args.log,
                args.max_actions,
                args.profile_dir,
                args.keep_open,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
