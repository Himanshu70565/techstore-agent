import os
import json

from google import genai
from playwright.sync_api import sync_playwright

from action import validate_action, execute_action


URL = "https://electronics-shopping-site.onrender.com/"


# Step 1: Check that the Gemini API key is configured.
if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError(
        "GEMINI_API_KEY is not configured. "
        "Set it in your terminal before running this file."
    )


# Step 2: Create the Gemini client.
client = genai.Client()


# Step 3: Ask Gemini to choose an action.
prompt = """
You are choosing the next action for a shopping browser agent.

User goal:
Find a Bluetooth keyboard under NZ$150.

Current page:
The TechStore homepage is open and no search has been performed.

Allowed actions:
1. search — requires a string value
2. inspect_page
3. finish

Return a JSON object with this structure:
{
    "action": "search",
    "value": "keyboard"
}

Choose the search action with value "keyboard".
Return only the JSON object, without Markdown code fences.
"""


response = client.models.generate_content(
    model="gemini-3.5-flash-lite",
    contents=prompt
)


# Step 4: Parse Gemini's response.
response_text = response.text

if not response_text:
    raise RuntimeError("Gemini returned an empty response.")

response_text = response_text.strip()

print("Gemini response:")
print(response_text)


try:
    # Handle responses wrapped in Markdown code fences.
    if response_text.startswith("```"):
        lines = response_text.splitlines()

        # Remove the opening fence, such as ```json.
        lines = lines[1:]

        # Remove the closing fence, if present.
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        response_text = "\n".join(lines).strip()

    # Convert the JSON text into a Python dictionary.
    decision = json.loads(response_text)

except (json.JSONDecodeError, TypeError) as error:
    print("\nCould not parse Gemini's response.")
    print("Raw response:", repr(response.text))
    raise RuntimeError(
        "Gemini did not return valid JSON."
    ) from error


# Step 5: Validate the proposed action before executing it.
approved, message = validate_action(decision)

print("\nValidation:", message)

if not approved:
    raise RuntimeError("Action rejected by validation.")


# Step 6: Open the browser and execute the approved action.
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    try:
        print("\nOpening the TechStore website...")

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Wait for the JavaScript application to render.
        page.wait_for_function(
            """
            () => {
                const app = document.querySelector("#app");
                return app && app.innerText.trim().length > 0;
            }
            """,
            timeout=30000
        )

        print("Website loaded successfully.")

        # Execute only the action that passed validation.
        result = execute_action(page, decision)

        print("\nExecution result:", result)

        # Display the resulting page text.
        print("\n--- PAGE AFTER ACTION ---")
        print(page.locator("body").inner_text()[:4000])

        input("\nPress Enter to close the browser...")

    finally:
        browser.close()
