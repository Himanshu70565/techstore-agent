"""Command-line entry point for the bounded TechStore research prototype."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from action import execute_action, validate_action
from browser import BrowserAgent
from llm import GeminiReasoner
from logger import ExperimentLogger
from models import Goal, PageState, Product
from prompts import (
    PROMPT_KEYS,
    PROMPTS,
    SCENARIO_WEBSITE_URLS,
    SHOPPING_GOAL,
)


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


def collect_headphone_catalogue(
    agent: BrowserAgent,
    inspect: bool,
) -> tuple[Product, ...]:
    """Search first, then optionally inspect every relevant product."""
    search_state = agent.search("headphones")
    catalogue = tuple(
        product
        for product in search_state.products
        if "headphone" in f"{product.name} {product.category}".lower()
    )
    if not inspect:
        return catalogue
    return agent.inspect_products(catalogue)


def run(
    scenario: str,
    log_path: str = "runs.jsonl",
    max_actions: int = 12,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    keep_open: bool = True,
    input_func: Any = input,
) -> dict[str, Any]:
    if scenario not in PROMPTS:
        choices = ", ".join(PROMPT_KEYS)
        raise ValueError(f"Unknown scenario {scenario!r}. Choose one of: {choices}")

    goal_text = SHOPPING_GOAL
    website_url = SCENARIO_WEBSITE_URLS[scenario]
    logger = ExperimentLogger(log_path)
    reasoner = GeminiReasoner()
    started = time.monotonic()
    goal = reasoner.parse_goal(goal_text)
    print("Running scenario:", scenario, "with goal:", goal_text, "goal: ", goal)
    selected: Product | None = None
    cart_verified = False
    cart_verification: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = []
    decision_feedback = ""
    catalogue: tuple[Product, ...] = ()
    inspected_product_ids: set[str] = set()
    proposed_product_id: str | None = None
    proposed_reason: str | None = None

    logger.record("run_started", scenario=scenario, goal=goal, website_url=website_url)
    with sync_playwright() as playwright:
        context = create_persistent_context(playwright, profile_dir)
        page = context.pages[0] if context.pages else context.new_page()
        agent = BrowserAgent(
            page,
            url=website_url,
            diagnostic=lambda event, **data: logger.record(event, **data),
        )
        try:
            agent.open()
            catalogue = collect_headphone_catalogue(
                agent,
                inspect=True,
            )
            inspected_product_ids = {product.id for product in catalogue}
            logger.record(
                "catalogue_inspected",
                catalogue=[product.__dict__ for product in catalogue],
                inspected_product_ids=sorted(inspected_product_ids),
            )
            for _ in range(max_actions):
                state = agent.observe()
                decision_state = (
                    catalogue
                    if scenario == "prompt_injection" and catalogue
                    else state.products
                )
                logger.record(
                    "decision_requested",
                    url=state.url,
                    product_count=len(decision_state),
                    inspected_product_ids=sorted(inspected_product_ids),
                )
                decision = reasoner.decide(
                    goal,
                    json.dumps(
                        {
                            "url": state.url,
                            "products": [product.__dict__ for product in decision_state],
                            "text": state.text,
                            "agent_feedback": decision_feedback,
                        },
                        ensure_ascii=True,
                    ),
                    max_actions - len(actions),
                    PROMPTS[scenario],
                )
                logger.record("decision_received", decision=decision)
                if decision.get("product_id"):
                    proposed_product_id = decision["product_id"]
                    proposed_reason = decision.get("reason")
                approved, message = validate_action(
                    decision,
                    goal,
                    state,
                    cart_verified=cart_verified,
                    catalogue=catalogue,
                    inspected_product_ids=inspected_product_ids,
                )
                logger.record("decision", decision=decision, approved=approved, validation=message)
                if not approved:
                    logger.record(
                        "validation_failure",
                        decision=decision,
                        validation=message,
                    )
                    if decision.get("action") == "finish":
                        decision_feedback = (
                            "The finish action was rejected because the cart task is incomplete. "
                            "Select a product, execute add_to_cart, and verify the cart before finishing."
                        )
                        continue
                    raise RuntimeError(message)
                if decision["action"] == "finish":
                    break
                try:
                    result = execute_action(agent, decision)
                except Exception as error:
                    logger.record(
                        "execution_exception",
                        decision=decision,
                        error=f"{type(error).__name__}: {error}",
                    )
                    raise
                actions.append(decision)
                logger.record("action_executed", decision=decision, result=result)
                if decision["action"] == "add_to_cart":
                    selected = next(
                        (
                            product
                            for product in catalogue or state.products
                            if product.id == decision["product_id"]
                        ),
                        None,
                    )
                    verification = agent.verify_cart(decision["product_id"])
                    logger.record("cart_verification", verification=verification)
                    if not verification.verified:
                        raise RuntimeError(verification.message)
                    cart_verified = True
                    cart_verification = verification.__dict__
                    break
            else:
                raise RuntimeError("Maximum action count reached")
        except Exception as error:
            logger.record(
                "exception",
                error=f"{type(error).__name__}: {error}",
            )
            raise
        finally:
            report = {
                "scenario": scenario,
                "goal": goal_text,
                "selected_product": selected.__dict__ if selected else None,
                "executed_product_id": selected.id if selected else None,
                "proposed_product_id": proposed_product_id,
                "proposed_reason": proposed_reason,
                "catalogue": [product.__dict__ for product in catalogue],
                "inspected_product_ids": sorted(inspected_product_ids),
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
    parser.add_argument("scenario", nargs="?", choices=PROMPT_KEYS)
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
    if args.scenario is None:
        parser.print_usage(sys.stderr)
        parser.exit(
            2,
            f"{parser.prog}: error: scenario is required; choose one of: "
            f"{', '.join(PROMPT_KEYS)}\n",
        )
    print(
        json.dumps(
            run(
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
