"""Controlled Playwright integration for the verified TechStore interface."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from models import CartVerification, Goal, PageState, Product

URL = "https://electronics-shopping-site.onrender.com/"


class BrowserAgent:
    def __init__(self, page: Any, url: str = URL) -> None:
        self.page = page
        self.url = url.rstrip("/") + "/"

    def open(self) -> None:
        self.page.goto(self.url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_function(
            """() => {
                const grid = document.querySelector('#product-grid');
                return grid && (grid.querySelectorAll('article.card').length > 0 ||
                    grid.textContent.includes('No products found'));
            }""",
            timeout=30_000,
        )

    def observe(self) -> PageState:
        products: list[Product] = []
        for card in self.page.locator("#product-grid article.card").all():
            link = card.locator("h3 a").first
            price_text = card.locator(".price").inner_text()
            products.append(
                Product(
                    id=str(link.get_attribute("href") or "").rsplit("/", 1)[-1],
                    name=link.inner_text().strip(),
                    category=card.locator(".muted").inner_text().split("·")[-1].strip(),
                    brand=card.locator(".muted").inner_text().split("·")[0].strip(),
                    price=float(price_text.replace("NZ$", "").replace(",", "").strip()),
                    description=card.locator(".card-body p").nth(2).inner_text().strip(),
                    availability=card.locator(".badge").inner_text().strip(),
                    url=str(link.get_attribute("href") or ""),
                )
            )
        if not products and self.page.locator("article.detail").count():
            product_id = self.page.url.rstrip("/").rsplit("/", 1)[-1]
            eyebrow = self.page.locator("article.detail .eyebrow").inner_text().split("·")
            products.append(
                Product(
                    id=product_id,
                    name=self.page.locator("article.detail h1").inner_text().strip(),
                    category=eyebrow[-1].strip(),
                    brand=eyebrow[0].strip(),
                    price=float(
                        self.page.locator("article.detail .price")
                        .inner_text()
                        .replace("NZ$", "")
                        .strip()
                    ),
                    description=self.page.locator("article.detail > div").last.locator("p").nth(2).inner_text().strip(),
                    availability=self.page.locator("article.detail .badge").inner_text().strip(),
                    url=f"/products/{product_id}",
                )
            )
        controls = tuple(
            self.page.locator("#app input, #app select, #app button").evaluate_all(
                """fields => fields.map(field =>
                    field.getAttribute('name') || field.id || field.getAttribute('role') || field.tagName
                )"""
            )
        )
        return PageState(
            url=self.page.url,
            text=self.page.locator("body").inner_text()[:8_000],
            products=tuple(products),
            controls=controls,
        )

    def search(self, value: str) -> PageState:
        field = self.page.locator('input[name="search"]')
        field.fill(value)
        self.page.get_by_role("button", name="Apply", exact=True).click()
        self.page.wait_for_function(
            """() => {
                const grid = document.querySelector('#product-grid');
                return grid && (grid.querySelectorAll('article.card').length > 0 ||
                    grid.textContent.includes('No products found'));
            }""",
            timeout=15_000,
        )
        return self.observe()

    def open_product(self, product_id: str) -> PageState:
        self.page.goto(f"{self.url}products/{quote(product_id, safe='')}", wait_until="domcontentloaded")
        self.page.wait_for_selector("article.detail", timeout=15_000)
        return self.observe()

    def compare_products(self, product_ids: list[str]) -> PageState:
        if len(product_ids) < 2:
            raise ValueError("At least two products are required for comparison")
        self.page.goto(f"{self.url}compare", wait_until="domcontentloaded")
        self.page.wait_for_selector("#compare-button", timeout=15_000)
        catalogue = self.page.evaluate(
            """async () => (await (await fetch('/api/products')).json()).products"""
        )
        categories = {
            str(product["id"]): str(product["category"])
            for product in catalogue
        }
        category = categories.get(product_ids[0])
        if category is None or any(categories.get(product_id) != category for product_id in product_ids):
            raise ValueError("Products must be known and from the same category")
        self.page.locator("#compare-category").select_option(label=category)
        self.page.wait_for_function(
            "() => document.querySelectorAll('#compare-one option').length >= 2",
            timeout=15_000,
        )
        self.page.locator("#compare-one").select_option(product_ids[0])
        self.page.locator("#compare-two").select_option(product_ids[1])
        if len(product_ids) == 3:
            self.page.locator("#compare-three").select_option(product_ids[2])
        self.page.locator("#compare-button").click()
        self.page.wait_for_selector("#compare-results table", timeout=15_000)
        return self.observe()

    def add_to_cart(self, product_id: str) -> None:
        button = self.page.locator(f'[data-add="{product_id}"]')
        if button.count() == 0:
            self.open_product(product_id)
            button = self.page.locator("#add-detail")
        button.first.click()
        self.page.wait_for_function(
            "() => document.querySelector('#cart-count')?.textContent !== '0'",
            timeout=15_000,
        )

    def verify_cart(self, expected_product_id: str | None = None) -> CartVerification:
        self.page.goto(f"{self.url}cart", wait_until="domcontentloaded")
        self.page.wait_for_selector("#cart-content", timeout=15_000)
        links = self.page.locator(".cart-item h3 a").all()
        names = tuple(link.inner_text().strip() for link in links)
        ids = tuple(str(link.get_attribute("href") or "").rsplit("/", 1)[-1] for link in links)
        verified = bool(names) and (
            expected_product_id is None or expected_product_id in ids
        )
        return CartVerification(
            verified=verified,
            item_names=names,
            message="Expected product is in the cart" if verified else "Cart does not contain the expected product",
        )