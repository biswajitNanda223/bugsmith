REPRO = '''
from app import cart_total

def test_none_discount():
    assert cart_total([{"sku": "B2", "price": 15.0, "qty": 2, "discount": None}]) == 30.0
'''

GOOD_PATCH = '''--- a/demo-app/app.py
+++ b/demo-app/app.py
@@ -16,7 +16,7 @@
 def cart_total(items: list[dict]) -> float:
     total = 0.0
     for item in items:
-        total += item["price"] * item["qty"] * (1 - item["discount"])
+        total += item["price"] * item["qty"] * (1 - (item["discount"] or 0))
     return round(total, 2)
 
 
'''

NOOP_PATCH = GOOD_PATCH.replace('(item["discount"] or 0)', '(item["discount"] or None)')
