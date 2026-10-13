
from playwright.sync_api import sync_playwright

URL = "https://electronics-shopping-site.onrender.com/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)

    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded")

    print("Page title:", page.title())
    print("Current URL:", page.url)

    browser.close()
