"""Command-line entry point for the bounded TechStore research prototype."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from .actions import execute_action, validate_action
from .browser import BrowserAgent
from .llm import GeminiReasoner
from .logger import ExperimentLogger, TerminalTrace
from .models import Goal, PageState, Product
from .prompts import (
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


def run_sequential_prompt_injection(
    agent: BrowserAgent,
    reasoner: GeminiReasoner,
    goal: Goal,
    goal_text: str,
    scenario: str,
    scenario_prompt: str,
    catalogue: tuple[Product, ...],
    max_actions: int,
    trace: TerminalTrace,
    logger: ExperimentLogger,
) -> tuple[Product | None, bool, dict[str, Any] | None, list[dict[str, Any]], set[str], str | None, str | None]:
    """Run the first-product-only decision condition without changing normal execution."""
    if not catalogue:
        raise RuntimeError("No headphone products were found for sequential inspection")

    current_state = agent.open_product(catalogue[0].id)
    inspected_ids = {catalogue[0].id}
    selected: Product | None = None
    cart_verified = False
    cart_verification: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = []
    proposed_product_id: str | None = None
    proposed_reason: str | None = None
    feedback = ""
    decision_budget = min(max_actions, max(1, len(catalogue) + 3))
    rejected_actions = 0
    previous_decision: tuple[Any, ...] | None = None

    def page_product_id(state: PageState) -> str:
        """Return the product represented by the current detail page."""
        marker = "/products/"
        if marker not in state.url:
            raise RuntimeError(
                f"Sequential workflow expected a product page, got {state.url}"
            )
        product_id = state.url.rstrip("/").rsplit("/", 1)[-1]
        if state.product_ids != {product_id}:
            raise RuntimeError(
                f"Browser state mismatch: URL product {product_id!r}, "
                f"PageState products {sorted(state.product_ids)!r}"
            )
        return product_id

    def open_next_uninspected() -> bool:
        nonlocal current_state
        next_product = next(
            (product for product in catalogue if product.id not in inspected_ids),
            None,
        )
        if next_product is None:
            return False
        navigation = {"action": "open_product", "product_id": next_product.id}
        try:
            result = execute_action(agent, navigation)
        except Exception as error:
            logger.record(
                "sequential_recovery_exception",
                decision=navigation,
                error=f"{type(error).__name__}: {error}",
            )
            trace.error(f"Sequential recovery failed: {type(error).__name__}: {error}")
            return False
        if not isinstance(result, PageState):
            logger.record(
                "sequential_recovery_failure",
                decision=navigation,
                reason="open_product did not return PageState",
            )
            return False
        current_state = result
        inspected_ids.add(next_product.id)
        actions.append(navigation)
        trace.executed += 1
        logger.record(
            "sequential_recovery_executed",
            decision=navigation,
            result=result,
        )
        return True

    for decision_number in range(decision_budget):
        current_product_id = page_product_id(current_state)
        inspected_ids.add(current_product_id)
        products_available = tuple(
            product for product in catalogue if product.id in inspected_ids
        )
        decision_context = json.dumps(
            {
                "url": current_state.url,
                "current_product_id": current_product_id,
                "products": [product.__dict__ for product in products_available],
                "text": current_state.text,
                "agent_feedback": feedback,
            },
            ensure_ascii=True,
        )
        logger.record(
            "sequential_decision_context",
            scenario=scenario,
            url=current_state.url,
            current_product_id=current_product_id,
            product_information=[product.__dict__ for product in products_available],
            descriptions_included=all(
                product.description in decision_context for product in products_available
            ),
        )
        decision = reasoner.decide(
            goal,
            decision_context,
            decision_budget - decision_number - 1,
            scenario_prompt,
            {
                "scenario": scenario,
                "url": current_state.url,
                "current_product_id": current_product_id,
                "goal": goal_text,
                "inspected_product_ids": sorted(inspected_ids),
            },
        )
        trace.proposed += 1
        proposed_product_id = decision.get("selected_product_id") or decision.get("product_id")
        proposed_reason = decision.get("reason")
        logger.record("sequential_decision", decision=decision)
        selected_id = decision.get("selected_product_id")
        all_inspected = inspected_ids == {product.id for product in catalogue}
        decision_signature = (
            decision.get("action"),
            decision.get("product_id"),
            tuple(decision.get("product_ids") or ()),
        )
        if decision_signature == previous_decision:
            if (
                all_inspected
                and decision.get("action") == "open_product"
            ):
                message = (
                    "All catalogue products are already inspected. Do not open "
                    "another product. Choose the product to add_to_cart now using "
                    "the observed prices and descriptions."
                )
                if selected_id in inspected_ids:
                    message = (
                        f"All catalogue products are inspected and you favoured "
                        f"{selected_id}. Propose add_to_cart for it now."
                    )
                logger.record(
                    "sequential_recovery_requested",
                    original_decision=decision,
                    feedback=message,
                )
                trace.error(message)
                feedback = message
                previous_decision = None
                continue
            message = "Repeated sequential decision; stopping the experiment"
            logger.record("sequential_stop", reason=message, decision=decision)
            trace.error(message)
            break
        previous_decision = decision_signature

        if (
            decision.get("action") == "open_product"
            and decision.get("product_id") in inspected_ids
        ):
            message = "Product was already inspected; choose an uninspected product"
            trace.rejected += 1
            rejected_actions += 1
            logger.record(
                "decision",
                decision=decision,
                approved=False,
                validation=message,
            )
            trace.error(message)
            if rejected_actions >= 3:
                logger.record("sequential_stop", reason="Validation retry limit reached")
                break
            feedback = message
            if open_next_uninspected():
                feedback = (
                    f"{message}. The workflow opened the next uninspected "
                    "catalogue product; inspect the current page and decide."
                )
            elif all_inspected:
                feedback = (
                    "All catalogue products are now inspected. Do not reopen a "
                    "product; choose the product to add_to_cart using the observed "
                    "prices and descriptions."
                )
                if selected_id in inspected_ids:
                    feedback = (
                        f"All catalogue products are now inspected. You favoured "
                        f"{selected_id}; propose add_to_cart for {selected_id} now."
                    )
                previous_decision = None
            continue

        approved, message = validate_action(
            decision,
            goal,
            current_state,
            cart_verified=cart_verified,
            catalogue=catalogue,
            inspected_product_ids=inspected_ids,
            allow_partial_inspection=True,
        )
        trace.section(
            "SEQUENTIAL ACTION VALIDATION",
            trace.gemini_requests,
            action=decision.get("action"),
            product_id=decision.get("product_id"),
            selected_product_id=decision.get("selected_product_id"),
            current_product_id=current_product_id,
            inspected_product_ids=sorted(inspected_ids),
            approved=approved,
            reason=message,
        )
        logger.record("decision", decision=decision, approved=approved, validation=message)
        if not approved:
            trace.rejected += 1
            rejected_actions += 1
            trace.error(f"Sequential action rejected: {message}")
            if rejected_actions >= 3:
                logger.record(
                    "sequential_stop",
                    reason="Validation retry limit reached",
                    validation=message,
                )
                break
            feedback = (
                f"Rejected: {message}. Choose an uninspected catalogue product "
                "or add_to_cart for an inspected product."
            )
            if (
                decision.get("action") == "search"
                and "product-detail page" in message
                and open_next_uninspected()
            ):
                feedback = (
                    "Search was unavailable on the product-detail page. The workflow "
                    "opened the next uninspected catalogue product; inspect it now."
                )
            continue

        trace.approved += 1
        if decision["action"] == "finish":
            break

        try:
            trace.section(
                "SEQUENTIAL PLAYWRIGHT EXECUTION",
                trace.gemini_requests,
                action=decision["action"],
                current_url_before=current_state.url,
                product_id=decision.get("product_id"),
            )
            result = execute_action(agent, decision)
            trace.executed += 1
            actions.append(decision)
            if isinstance(result, PageState):
                current_state = result
            logger.record("action_executed", decision=decision, result=result)
            trace.section(
                "SEQUENTIAL PLAYWRIGHT RESULT",
                trace.gemini_requests,
                succeeded=True,
                current_url_after=current_state.url,
                result=result,
            )
        except Exception as error:
            logger.record(
                "execution_exception",
                decision=decision,
                error=f"{type(error).__name__}: {error}",
            )
            trace.error(f"Sequential Playwright execution failed: {type(error).__name__}: {error}")
            feedback = (
                f"The previous browser action failed: {type(error).__name__}: {error}. "
                "Do not repeat an action that is unavailable on the current page."
            )
            continue

        if decision["action"] == "open_product":
            inspected_ids.add(decision["product_id"])
            continue
        if decision["action"] == "add_to_cart":
            candidate = next(
                product for product in catalogue if product.id == decision["product_id"]
            )
            try:
                verification = agent.verify_cart(decision["product_id"])
            except Exception as error:
                logger.record(
                    "cart_verification_exception",
                    decision=decision,
                    error=f"{type(error).__name__}: {error}",
                )
                trace.error(f"Cart verification failed: {type(error).__name__}: {error}")
                feedback = (
                    f"Cart verification failed: {type(error).__name__}: {error}. "
                    "The task is not complete; recover with a supported action."
                )
                continue
            cart_verification = verification.__dict__
            cart_verified = verification.verified
            logger.record("cart_verification", verification=verification)
            trace.section(
                "SEQUENTIAL CART VERIFICATION",
                trace.gemini_requests,
                verified=verification.verified,
                actual_cart_contents=verification,
            )
            if not verification.verified:
                trace.error(f"Cart verification rejected the result: {verification.message}")
                feedback = (
                    f"Cart verification failed: {verification.message}. "
                    "Do not finish until the expected product is verified."
                )
                continue
            selected = candidate
            break
    else:
        message = f"Sequential decision budget exhausted after {decision_budget} decisions"
        logger.record("sequential_stop", reason=message)
        trace.error(message)

    return (
        selected,
        cart_verified,
        cart_verification,
        actions,
        inspected_ids,
        proposed_product_id,
        proposed_reason,
    )


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
    trace = TerminalTrace()
    reasoner = GeminiReasoner(trace=trace)
    started = time.monotonic()
    goal = reasoner.parse_goal(
        goal_text,
        {"scenario": scenario, "operation": "parse_goal", "goal": goal_text},
    )
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

    def browser_diagnostic(event: str, **data: Any) -> None:
        logger.record(event, **data)
        trace.section(
            "PLAYWRIGHT BROWSER EVENT",
            trace.gemini_requests,
            event=event,
            **data,
        )

    with sync_playwright() as playwright:
        context = create_persistent_context(playwright, profile_dir)
        page = context.pages[0] if context.pages else context.new_page()
        agent = BrowserAgent(
            page,
            url=website_url,
            diagnostic=browser_diagnostic,
        )
        try:
            agent.open()
            sequential_result = None
            if scenario == "sequential_prompt_injection":
                catalogue = collect_headphone_catalogue(agent, inspect=False)
            else:
                catalogue = collect_headphone_catalogue(agent, inspect=True)
                inspected_product_ids = {product.id for product in catalogue}
            logger.record(
                "catalogue_inspected",
                catalogue=[product.__dict__ for product in catalogue],
                inspected_product_ids=sorted(inspected_product_ids),
                descriptions=[product.description for product in catalogue],
            )
            if scenario == "sequential_prompt_injection":
                sequential_result = run_sequential_prompt_injection(
                    agent,
                    reasoner,
                    goal,
                    goal_text,
                    scenario,
                    PROMPTS[scenario],
                    catalogue,
                    max_actions,
                    trace,
                    logger,
                )
                (
                    selected,
                    cart_verified,
                    cart_verification,
                    actions,
                    inspected_product_ids,
                    proposed_product_id,
                    proposed_reason,
                ) = sequential_result
            for _ in ([] if sequential_result is not None else range(max_actions)):
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
                decision_context = json.dumps(
                    {
                        "url": state.url,
                        "products": [product.__dict__ for product in decision_state],
                        "text": state.text,
                        "agent_feedback": decision_feedback,
                    },
                    ensure_ascii=True,
                )
                logger.record(
                    "gemini_request_context",
                    url=state.url,
                    product_ids=[product.id for product in decision_state],
                    descriptions_included=all(
                        product.description in decision_context
                        for product in decision_state
                    ),
                    descriptions=[
                        {
                            "product_id": product.id,
                            "description": product.description,
                        }
                        for product in decision_state
                    ],
                )
                decision = reasoner.decide(
                    goal,
                    decision_context,
                    max_actions - len(actions),
                    PROMPTS[scenario],
                    {
                        "scenario": scenario,
                        "url": state.url,
                        "product_ids": [product.id for product in decision_state],
                        "product_names": [product.name for product in decision_state],
                        "goal": goal_text,
                        "inspected_product_ids": sorted(inspected_product_ids),
                    },
                )
                trace.proposed += 1
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
                trace.section(
                    "ACTION VALIDATION",
                    trace.gemini_requests,
                    action=decision.get("action"),
                    product_id=decision.get("product_id"),
                    arguments=decision,
                    observed_product_ids=sorted(state.product_ids),
                    catalogue_product_ids=[product.id for product in catalogue],
                    inspected_product_ids=sorted(inspected_product_ids),
                    requested_goal=goal.raw,
                    requested_action=goal.requested_action,
                    approved=approved,
                    reason=message,
                )
                if not approved:
                    trace.error("Playwright will not execute the rejected action")
                logger.record("decision", decision=decision, approved=approved, validation=message)
                if not approved:
                    trace.rejected += 1
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
                trace.approved += 1
                if decision["action"] == "finish":
                    break
                try:
                    before_url = state.url
                    target = next(
                        (product for product in catalogue if product.id == decision.get("product_id")),
                        None,
                    )
                    trace.section(
                        "PLAYWRIGHT EXECUTION",
                        trace.gemini_requests,
                        action=decision["action"],
                        current_url_before=before_url,
                        product_id=decision.get("product_id"),
                        product_name=target.name if target else None,
                        operation=f"execute_action({decision['action']})",
                    )
                    result = execute_action(agent, decision)
                    after_state = agent.observe()
                    trace.executed += 1
                    trace.section(
                        "PLAYWRIGHT RESULT",
                        trace.gemini_requests,
                        succeeded=True,
                        current_url_after=after_state.url,
                        result=result,
                        observed_page_state=after_state,
                    )
                except Exception as error:
                    trace.error(f"Playwright execution failed: {type(error).__name__}: {error}")
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
                    trace.section(
                        "CART VERIFICATION",
                        trace.gemini_requests,
                        verified=verification.verified,
                        actual_cart_contents=verification,
                    )
                    logger.record("cart_verification", verification=verification)
                    if not verification.verified:
                        raise RuntimeError(verification.message)
                    cart_verified = True
                    cart_verification = verification.__dict__
                    break
            else:
                if sequential_result is not None:
                    pass
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
            trace.section(
                "FINAL EXECUTION SUMMARY",
                trace.gemini_requests,
                scenario=scenario,
                goal=goal_text,
                gemini_requests=trace.gemini_requests,
                actions_proposed=trace.proposed,
                actions_approved=trace.approved,
                actions_rejected=trace.rejected,
                actions_executed=trace.executed,
                inspected_products=sorted(inspected_product_ids),
                proposed_product_id=proposed_product_id,
                selected_product=selected,
                cart_verified=cart_verified,
                errors=trace.errors,
            )
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
