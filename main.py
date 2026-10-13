
from playwright.sync_api import sync_playwright

URL = "https://electronics-shopping-site.onrender.com/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    # 1. Open the website
    page.goto(URL, wait_until="domcontentloaded")

    # 2. Collect page metadata
    print("\n--- PAGE INFORMATION ---")
    print("Title:", page.title())
    print("URL:", page.url)

    # 3. Wait for JavaScript-rendered content
    try:
        page.wait_for_function(
            """
            () => {
                const app = document.querySelector("#app");
                return app && app.innerText.trim().length > 0;
            }
            """,
            timeout=15000
        )
        print("\nApplication content loaded.")
    except Exception:
        print("\nWarning: App content did not appear within 15 seconds.")

    # 4. Extract visible page text
    print("\n--- VISIBLE PAGE TEXT ---")
    page_text = page.locator("body").inner_text()
    print(page_text[:5000])

    # 5. Extract buttons
    print("\n--- BUTTONS ---")
    buttons = page.get_by_role("button").all()
    print("Buttons found:", len(buttons))

    for button in buttons:
        text = button.inner_text().strip()
        if text:
            print("Button:", text)

    # 6. Extract links
    print("\n--- LINKS ---")
    links = page.get_by_role("link").all()
    print("Links found:", len(links))

    for link in links:
        text = link.inner_text().strip()
        href = link.get_attribute("href")

        if text:
            print(f"Link: {text} | URL: {href}")

    # 7. Extract search inputs
    print("\n--- SEARCH INPUTS ---")
    inputs = page.locator("input").all()
    print("Input elements found:", len(inputs))

    for field in inputs:
        print(
            "Type:", field.get_attribute("type"),
            "| Placeholder:", field.get_attribute("placeholder"),
            "| Name:", field.get_attribute("name"),
            "| Aria-label:", field.get_attribute("aria-label")
        )

    print("\n--- INPUT DETAILS ---")

    for field in page.locator("#app input").all():
        print({
            "type": field.get_attribute("type"),
            "placeholder": field.get_attribute("placeholder"),
            "name": field.get_attribute("name"),
            "aria-label": field.get_attribute("aria-label"),
            "id": field.get_attribute("id")
        })

    # 8. Perform a search
    print("\n--- PERFORMING SEARCH ---")

    search_field = page.locator('input[name="search"]')

    search_field.fill("keyboard")

    print("Search term entered: keyboard")

    # Inspect the updated page
    print("\n--- PAGE AFTER SEARCH ---")
    print(page.locator("body").inner_text()[:5000])

    # 9. Keep the browser open until inspection is complete
    input("\nPress Enter to close the browser...")
    browser.close()
