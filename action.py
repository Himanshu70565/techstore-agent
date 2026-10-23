
ALLOWED_ACTIONS = {"search", "inspect_page", "finish"}


def validate_action(decision):
    if not isinstance(decision, dict):
        return False, "Decision must be a dictionary"

    action = decision.get("action")

    if action not in ALLOWED_ACTIONS:
        return False, f"Action not allowed: {action}"

    if action == "search":
        value = decision.get("value")

        if not isinstance(value, str) or not value.strip():
            return False, "Search requires a non-empty string"

    return True, "Action approved"


def execute_action(page, decision):
    """Execute an action that has already passed validation."""

    action = decision["action"]

    if action == "search":
        search_field = page.locator('input[name="search"]')
        search_field.fill(decision["value"])

        # The TechStore page may require the Apply button.
        apply_button = page.get_by_role(
            "button", name="Apply", exact=True
        )

        if apply_button.count() > 0:
            apply_button.click()

        return "Search action executed"

    elif action == "inspect_page":
        return page.locator("body").inner_text()[:4000]

    elif action == "finish":
        return "Agent finished"
