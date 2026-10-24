"""Typed data exchanged between the agent's layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Goal:
    raw: str
    categories: tuple[str, ...]
    objective: str = "minimise_price"
    maximum_price: float | None = None
    required_attributes: tuple[str, ...] = ()
    requested_action: str = "add_to_cart"


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    category: str
    brand: str
    price: float
    description: str
    availability: str = ""
    specifications: dict[str, str] = field(default_factory=dict)
    url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Product:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            category=str(data["category"]),
            brand=str(data.get("brand", "")),
            price=float(data["price"]),
            description=str(data.get("description", "")),
            availability=str(data.get("availability", "")),
            specifications={str(k): str(v) for k, v in data.get("specifications", {}).items()},
            url=str(data.get("url", "")),
        )


@dataclass(frozen=True)
class CartVerification:
    verified: bool
    item_names: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class PageState:
    url: str
    text: str
    products: tuple[Product, ...] = ()
    controls: tuple[str, ...] = ()
    cart: CartVerification | None = None

    @property
    def product_ids(self) -> set[str]:
        return {product.id for product in self.products}


def as_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: as_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (tuple, set)):
        return [as_jsonable(item) for item in value]
    if isinstance(value, list):
        return [as_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): as_jsonable(item) for key, item in value.items()}
    return value
