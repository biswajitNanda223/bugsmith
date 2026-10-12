"""Tiny demo service with a seeded production bug (Milestone 0).

Bug: `cart_total` crashes with a TypeError when an item has no `discount`
(the API sends `null`), which happens for items added before discounts existed.
"""
from fastapi import FastAPI

app = FastAPI(title="demo-shop")

CARTS = {
    "c1": [{"sku": "A1", "price": 20.0, "qty": 2, "discount": 0.1}],
    "c2": [{"sku": "B2", "price": 15.0, "qty": 1, "discount": None}],  # legacy item
}


def cart_total(items: list[dict]) -> float:
    total = 0.0
    for item in items:
        total += item["price"] * item["qty"] * (1 - item["discount"])
    return round(total, 2)


@app.get("/cart/{cart_id}/total")
def get_total(cart_id: str):
    return {"cart_id": cart_id, "total": cart_total(CARTS[cart_id])}
