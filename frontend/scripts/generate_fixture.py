"""Export only synthetic business aggregates; never access environment or databases."""

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts/seed_demo.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
business = {name: [] for name in seed.BUSINESS_TABLES}
seed.make_business(business, 320, 20260922)
groups = defaultdict(lambda: {"amount": 0, "cost": 0, "count": 0, "quantity": 0})
for order in business["sales_order"]:
    if order["status"] != "COMPLETED":
        continue
    key = (order["order_date"].strftime("%Y-%m"), order["region_id"], order["product_id"])
    row = groups[key]
    row["amount"] += int(order["amount"] * 100)
    row["cost"] += int(order["cost"] * 100)
    row["count"] += 1
    row["quantity"] += order["quantity"]
payload = {
    "version": seed.DATA_VERSION,
    "completeThrough": seed.COMPLETE_THROUGH.isoformat(),
    "availableFrom": seed.AVAILABLE_FROM.isoformat(),
    "orderCount": len(business["sales_order"]),
    "regions": [{"id": row["id"], "name": row["name"]} for row in business["sales_region"]],
    "products": [{"id": row["id"], "name": row["name"], "category": row["category"]} for row in business["product"]],
    "cells": [{"month": key[0], "region": key[1], "product": key[2], **row} for key, row in sorted(groups.items())],
}
target = ROOT / "frontend/src/data/fixture.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"Exported {len(groups)} aggregate cells from {len(business['sales_order'])} synthetic orders.")
