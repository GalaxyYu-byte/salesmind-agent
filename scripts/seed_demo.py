"""Generate deterministic MySQL demo fixtures without connecting to a database.

Run with Python 3.12: python scripts/seed_demo.py
Only the explicitly named *_demo schemas appear in generated import statements.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import hashlib
import hmac
import json
import random
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DATE = date(2026, 9, 22)
AVAILABLE_FROM = date(2024, 2, 1)
COMPLETE_THROUGH = REFERENCE_DATE - timedelta(days=1)
STAMP = datetime(2026, 9, 22, 6, tzinfo=UTC)
DATA_VERSION = 2026092201
DEMO_PASSWORD = "DemoOnly!2026"
PASSWORD_ITERATIONS = 600_000
CENT = Decimal("0.01")
BUSINESS_DB = "sales_data_demo"
APP_DB = "agent_app_demo"
METRICS = [
    "sales_amount",
    "order_count",
    "quantity",
    "gross_profit",
    "gross_margin",
    "avg_order_value",
    "product_share",
    "growth_rate",
]
BASIC_METRICS = [
    metric for metric in METRICS if metric not in {"gross_profit", "gross_margin"}
]
BUSINESS_TABLES = [
    "sales_region",
    "sales_rep",
    "product",
    "sales_order",
    "dataset_version",
]
APP_TABLES = [
    "app_user",
    "user_region_scope",
    "chat_session",
    "analysis_run",
    "chat_message",
    "run_event",
    "result_artifact",
    "tool_execution",
]


def uid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"salesmind-demo-v1:{name}"))


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def password_hash(username: str) -> str:
    # Deterministic salt is for reproducible fixtures ONLY. Real accounts need random salts.
    salt = "demo_" + hashlib.sha256(username.encode("ascii")).hexdigest()[:24]
    key = hashlib.pbkdf2_hmac(
        "sha256", DEMO_PASSWORD.encode(), salt.encode(), PASSWORD_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${base64.b64encode(key).decode()}"
    )


def verify_demo_password(password: str, encoded: str) -> bool:
    """Document the fixture format; this is not an implemented login endpoint."""
    algorithm, iterations, salt, expected = encoded.split("$")
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), int(iterations)
    )
    return hmac.compare_digest(base64.b64encode(actual).decode(), expected)


def months() -> list[date]:
    result = []
    year, month = AVAILABLE_FROM.year, AVAILABLE_FROM.month
    while (year, month) <= (REFERENCE_DATE.year, REFERENCE_DATE.month):
        result.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def sales_totals(orders: list[dict], **filters: object) -> dict:
    eligible = [
        order
        for order in orders
        if order["status"] == "COMPLETED"
        and all(
            order["order_date"].strftime("%Y-%m") == value
            if key == "month"
            else order[key] == value
            for key, value in filters.items()
        )
    ]
    amount = sum((row["amount"] for row in eligible), Decimal(0))
    cost = sum((row["cost"] for row in eligible), Decimal(0))
    return {
        "sales_amount": f"{amount:.2f}",
        "cost": f"{cost:.2f}",
        "gross_profit": f"{amount - cost:.2f}",
        "order_count": len(eligible),
        "quantity": sum(row["quantity"] for row in eligible),
    }


def make_business(
    data: dict[str, list[dict]], orders_per_month: int, seed: int
) -> None:
    for index, name in enumerate(["华东", "华南", "华北", "西南", "华中"], 1):
        data["sales_region"].append(
            {
                "id": index,
                "name": name,
                "parent_id": None,
                "active": 1,
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        )
    reps = [
        (1, "演示林晨", 1, 1),
        (2, "演示周宁", 2, 1),
        (3, "演示陈嘉", 2, 1),
        (4, "演示许悦", 2, 1),
        (5, "演示赵毅", 3, 1),
        (6, "演示苏晴", 3, 1),
        (7, "演示王凯", 4, 1),
        (8, "演示张然", 4, 0),
        (9, "演示空区域人员", 5, 1),
        (10, "演示边界测试员", 1, 1),
    ]
    for rep_id, name, region, active in reps:
        data["sales_rep"].append(
            {
                "id": rep_id,
                "name": name,
                "region_id": region,
                "active": active,
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        )
    products = [
        ("笔记本电脑", "数码产品"),
        ("显示器", "数码产品"),
        ("机械键盘", "数码产品"),
        ("空气净化器", "家用电器"),
        ("电水壶", "家用电器"),
        ("通勤背包", "服装配饰"),
        ("纯棉上衣", "服装配饰"),
        ("旧款保温杯", "其他"),
        ("零基期测试商品", "其他"),
        ("八月上市新品", "数码产品"),
        ("未售出商品", "其他"),
        ("同比环比测试商品", "其他"),
        ("金额精度测试商品", "其他"),
        ("亏损测试商品", "其他"),
    ]
    for product_id, (name, category) in enumerate(products, 1):
        data["product"].append(
            {
                "id": product_id,
                "sku": f"DEMO-{product_id:03}",
                "name": name,
                "category": category,
                "active": int(product_id != 8),
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        )

    def add_order(
        order_date: date,
        rep_id: int,
        product_id: int,
        quantity: int,
        unit_price: object,
        cost: object,
        status: str = "COMPLETED",
        label: str | None = None,
    ) -> None:
        rep = data["sales_rep"][rep_id - 1]
        # Rep 2 moved from East to South in July 2026; old order regions never change.
        region_id = (
            1 if rep_id == 2 and order_date < date(2026, 7, 1) else rep["region_id"]
        )
        order_id = len(data["sales_order"]) + 1
        data["sales_order"].append(
            {
                "id": order_id,
                "order_no": f"DEMO-{label}" if label else f"DEMO-{order_id:07}",
                "rep_id": rep_id,
                "region_id": region_id,
                "product_id": product_id,
                "customer_name": f"虚构客户-{(order_id % 40) + 1:02}",
                "quantity": quantity,
                "unit_price": money(unit_price),
                "amount": money(money(unit_price) * quantity),
                "cost": money(cost),
                "status": status,
                "order_date": order_date,
                "created_at": datetime.combine(
                    order_date, datetime.min.time(), tzinfo=UTC
                ),
                "updated_at": STAMP,
            }
        )

    rng = random.Random(seed)
    prices = [
        Decimal(value)
        for value in [
            "4999.00",
            "1299.00",
            "299.00",
            "899.00",
            "129.00",
            "199.00",
            "79.90",
            "59.90",
        ]
    ]
    for month in months():
        final_day = min(
            calendar.monthrange(month.year, month.month)[1],
            COMPLETE_THROUGH.day if month.year == 2026 and month.month == 9 else 31,
        )
        rep_ids = list(range(1, 8 if month >= date(2026, 7, 1) else 9))
        product_ids = list(range(1, 8 if month >= date(2026, 7, 1) else 9))
        for _ in range(orders_per_month):
            product_id = rng.choice(product_ids)
            quantity = rng.randint(1, 5)
            price = money(
                prices[product_id - 1]
                * rng.choice([Decimal(1), Decimal("0.95"), Decimal("0.90")])
            )
            status = rng.choices(
                ["COMPLETED", "REFUNDED", "CANCELLED"], weights=[90, 6, 4]
            )[0]
            add_order(
                date(month.year, month.month, rng.randint(1, final_day)),
                rng.choice(rep_ids),
                product_id,
                quantity,
                price,
                money(price * quantity * Decimal("0.65")),
                status,
            )

    fixtures = [
        # Dedicated products/rep make these assertions independent of random volume.
        ("2025-08-10", 10, 12, 1, "100", "60", "COMPLETED", "YOY-BASE"),
        ("2026-07-10", 10, 12, 1, "80", "50", "COMPLETED", "MOM-BASE"),
        ("2026-08-10", 10, 12, 1, "120", "70", "COMPLETED", "CURRENT"),
        ("2026-08-11", 10, 12, 1, "900", "500", "REFUNDED", "REFUNDED"),
        ("2026-08-12", 10, 12, 1, "800", "400", "CANCELLED", "CANCELLED"),
        ("2026-07-15", 10, 9, 1, "0", "0", "COMPLETED", "ZERO-BASE"),
        ("2026-08-15", 10, 9, 5, "100", "300", "COMPLETED", "ZERO-CURRENT"),
        ("2026-08-20", 10, 10, 1, "990", "600", "COMPLETED", "NEW-PRODUCT"),
        ("2026-08-21", 10, 13, 3, "19.99", "30.01", "COMPLETED", "DECIMAL-1"),
        ("2026-08-22", 10, 13, 3, "0.10", "0.10", "COMPLETED", "DECIMAL-2"),
        ("2026-08-23", 10, 14, 1, "50", "70", "COMPLETED", "LOSS"),
        ("2024-02-29", 10, 13, 1, "29", "10", "COMPLETED", "LEAP-DAY"),
        ("2024-03-01", 10, 13, 1, "31", "10", "COMPLETED", "MARCH-START"),
        ("2025-12-31", 10, 13, 1, "12.31", "5", "COMPLETED", "YEAR-END"),
        ("2026-01-01", 10, 13, 1, "10.01", "5", "COMPLETED", "YEAR-START"),
        ("2026-08-31", 10, 13, 1, "8.31", "3", "COMPLETED", "MONTH-END"),
        ("2026-09-01", 10, 13, 1, "9.01", "3", "COMPLETED", "MONTH-START"),
        ("2026-09-21", 10, 13, 1, "9.21", "3", "COMPLETED", "WATERMARK"),
        ("2026-06-30", 2, 1, 1, "1000", "650", "COMPLETED", "BEFORE-TRANSFER"),
        ("2026-07-01", 2, 1, 1, "1000", "650", "COMPLETED", "AFTER-TRANSFER"),
        ("2026-06-29", 8, 8, 1, "59.90", "30", "COMPLETED", "INACTIVE-HISTORY"),
    ]
    for day, rep_id, product_id, quantity, price, cost, status, label in fixtures:
        add_order(
            date.fromisoformat(day),
            rep_id,
            product_id,
            quantity,
            price,
            cost,
            status,
            label,
        )
    data["dataset_version"].append(
        {
            "id": 1,
            "version": DATA_VERSION,
            "refreshed_at": STAMP,
            "available_from": AVAILABLE_FROM,
            "complete_through": COMPLETE_THROUGH,
        }
    )


def scope_for(user: dict, regions: list[int], *, old_permission: bool = False) -> dict:
    return {
        "user_id": user["id"],
        "allowed_region_ids": sorted(regions)
        if user["role"] == "SALES_MANAGER"
        else None,
        "allowed_rep_ids": [user["rep_id"]] if user["role"] == "SALES_REP" else None,
        "allowed_metrics": sorted(user["allowed_metrics_json"]),
        "permission_version": 1 if old_permission else user["permission_version"],
    }


def make_accounts(data: dict[str, list[dict]]) -> dict[str, dict]:
    specifications = [
        ("demo_director", "演示销售总监", "SALES_DIRECTOR", None, [], METRICS, 1, 1),
        (
            "demo_manager",
            "演示双区域经理",
            "SALES_MANAGER",
            None,
            [1, 2],
            METRICS,
            1,
            1,
        ),
        ("demo_rep_east", "演示华东销售", "SALES_REP", 1, [], BASIC_METRICS, 1, 1),
        ("demo_rep_south", "演示华南销售", "SALES_REP", 3, [], BASIC_METRICS, 1, 1),
        ("demo_fixture", "演示固定用例销售", "SALES_REP", 10, [], METRICS, 1, 1),
        ("demo_no_region", "演示无区域经理", "SALES_MANAGER", None, [], METRICS, 1, 1),
        ("demo_disabled", "演示停用账号", "SALES_REP", 5, [], BASIC_METRICS, 0, 1),
        (
            "demo_inactive_rep",
            "演示已停用人员",
            "SALES_REP",
            8,
            [],
            BASIC_METRICS,
            1,
            1,
        ),
        ("demo_revoked", "演示权限收缩经理", "SALES_MANAGER", None, [1], METRICS, 1, 2),
        ("demo_no_metrics", "演示无指标权限", "SALES_MANAGER", None, [1], [], 1, 1),
    ]
    users = {}
    for (
        username,
        name,
        role,
        rep_id,
        regions,
        metrics,
        active,
        version,
    ) in specifications:
        user = {
            "id": uid(username),
            "username": username,
            "display_name": name,
            "password_hash": password_hash(username),
            "role": role,
            "rep_id": rep_id,
            "allowed_metrics_json": metrics,
            "permission_version": version,
            "active": active,
            "created_at": STAMP,
            "updated_at": STAMP,
        }
        data["app_user"].append(user)
        users[username] = user
        for region_id in regions:
            data["user_region_scope"].append(
                {"user_id": user["id"], "region_id": region_id, "created_at": STAMP}
            )
    return users


def make_application(data: dict[str, list[dict]], users: dict[str, dict]) -> dict:
    cases = [
        ("completed", "demo_fixture", "completed"),
        ("partial", "demo_director", "partial"),
        ("forbidden", "demo_rep_east", "failed"),
        ("cancelled", "demo_manager", "cancelled"),
        ("awaiting_input", "demo_manager", "awaiting_input"),
        ("interrupted", "demo_director", "interrupted"),
        ("expired", "demo_fixture", "completed"),
        ("permission_changed", "demo_revoked", "completed"),
    ]
    manifest = {}
    totals = sales_totals(
        data["sales_order"], rep_id=10, product_id=12, month="2026-08"
    )

    def build_case(case: str, username: str, status: str) -> None:
        user = users[username]
        created = STAMP - timedelta(days=9) if case == "expired" else STAMP
        old_permission = case == "permission_changed"
        regions = [1, 2] if user["role"] == "SALES_MANAGER" else []
        scope = scope_for(user, regions, old_permission=old_permission)
        scope_hash = digest(scope)
        session_id, run_id = uid(f"session:{case}"), uid(f"run:{case}")
        question = "分析边界测试员 2026 年 8 月同比环比测试商品的销售额，并画图。"
        if case == "forbidden":
            question = "查看华北所有销售员的销售额。"
        elif case == "awaiting_input":
            question = "分析一下销售额。"
        request = {"question": question}
        terminal = status in {"completed", "partial", "failed", "cancelled"}
        completed = status in {"completed", "partial"}
        error = {
            "forbidden": "SCOPE_FORBIDDEN",
            "partial": "CHART_BUILD_FAILED",
            "interrupted": "PROCESS_RESTART",
        }.get(case)
        run = {
            "id": run_id,
            "session_id": session_id,
            "owner_id": user["id"],
            "request_id": f"demo-v1-{case}",
            "input_hash": digest({"session_id": session_id, **request}),
            "request_json": request,
            "status": status,
            "stage": "Answer"
            if completed
            else {
                "awaiting_input": "Clarify",
                "interrupted": "Query",
                "forbidden": "ValidatePlan",
            }.get(case),
            "attempt": 0 if case == "cancelled" else 1,
            "reference_date": created.date(),
            "timezone": "Asia/Shanghai",
            "graph_version": "demo-v1",
            "metric_version": "v1",
            "scope_hash": scope_hash,
            "permission_version": scope["permission_version"],
            "data_version": DATA_VERSION
            if completed or case == "interrupted"
            else None,
            "last_event_seq": 0,
            "last_message_seq": 0,
            "replay_floor_seq": 0,
            "node_steps": 6 if completed else 0,
            "tool_calls": 0,
            "model_calls": 0,
            "active_elapsed_ms": 5000 if completed else 0,
            "error_code": error,
            "error_message": "模拟失败，用于测试。" if error else None,
            "created_at": created,
            "updated_at": created + timedelta(seconds=5),
            "started_at": None if case == "cancelled" else created,
            "finished_at": created + timedelta(seconds=5) if terminal else None,
            "paused_until": None if terminal else created + timedelta(days=1),
        }
        session = {
            "id": session_id,
            "owner_id": user["id"],
            "title": f"测试样例：{case}",
            "active_run_id": None,
            "graph_version": "demo-v1",
            "context_summary": None,
            "context_scope_hash": None,
            "context_permission_version": None,
            "created_at": created,
            "updated_at": created + timedelta(seconds=5),
        }
        data["chat_session"].append(session)
        data["analysis_run"].append(run)
        tick_count = 0

        def tick() -> datetime:
            nonlocal tick_count
            tick_count += 1
            return created + timedelta(milliseconds=tick_count * 100)

        def message(role: str, kind: str, key: str, content: str) -> None:
            run["last_message_seq"] += 1
            data["chat_message"].append(
                {
                    "id": uid(f"message:{case}:{key}"),
                    "run_id": run_id,
                    "session_id": session_id,
                    "owner_id": user["id"],
                    "seq": run["last_message_seq"],
                    "message_key": key,
                    "role": role,
                    "kind": kind,
                    "content": content,
                    "created_at": tick(),
                }
            )

        def event(kind: str, payload: dict) -> None:
            run["last_event_seq"] += 1
            data["run_event"].append(
                {
                    "run_id": run_id,
                    "seq": run["last_event_seq"],
                    "schema_version": 1,
                    "event_type": kind,
                    "payload_json": payload,
                    "created_at": tick(),
                }
            )

        def artifact(key: str, kind: str, payload: dict, source_ids: list[str]) -> str:
            artifact_id = uid(f"artifact:{case}:{key}")
            saved_at = tick()
            schema = {
                "source_artifact_ids": source_ids,
                "scope": scope,
                "fixture": True,
            }
            if kind == "DATASET":
                schema["columns"] = [
                    {
                        "name": key,
                        "type": "integer"
                        if isinstance(value, int)
                        else "decimal_string",
                    }
                    for key, value in totals.items()
                ]
            data["result_artifact"].append(
                {
                    "id": artifact_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "owner_id": user["id"],
                    "artifact_key": key,
                    "kind": kind,
                    "schema_version": 1,
                    "schema_json": schema,
                    "payload_json": payload,
                    "scope_hash": scope_hash,
                    "permission_version": scope["permission_version"],
                    "data_version": DATA_VERSION,
                    "metric_version": "v1",
                    "row_count": 1 if kind == "DATASET" else None,
                    "byte_size": len(canonical(schema).encode())
                    + len(canonical(payload).encode()),
                    "expires_at": saved_at + timedelta(days=7),
                    "created_at": saved_at,
                }
            )
            return artifact_id

        def tool(
            task_id: str, name: str, tool_status: str, code: str | None = None
        ) -> None:
            run["tool_calls"] += 1
            started_at = tick()
            data["tool_execution"].append(
                {
                    "id": len(data["tool_execution"]) + 1,
                    "run_id": run_id,
                    "task_id": task_id,
                    "run_attempt": 1,
                    "attempt": 1,
                    "tool_name": name,
                    "fingerprint": digest(
                        {
                            "task": task_id,
                            "scope": scope,
                            "query": {
                                "rep_id": 10,
                                "product_id": 12,
                                "month": "2026-08",
                            },
                            "data_version": DATA_VERSION,
                        }
                    ),
                    "cache_hit": 0,
                    "duration_ms": 50,
                    "status": tool_status,
                    "error_code": code,
                    "started_at": started_at,
                    "finished_at": started_at + timedelta(milliseconds=50),
                }
            )

        message("user", "question", "initial-question", question)
        if case != "cancelled":
            event("run.started", {"fixture": True})
        if completed:
            event("tool.started", {"task_id": "q_current", "tool": "query_sales"})
            tool("q_current", "query_sales", "succeeded")
            dataset_id = artifact(
                "q_current:dataset", "DATASET", {"rows": [totals]}, []
            )
            event(
                "tool.completed",
                {
                    "task_id": "q_current",
                    "tool": "query_sales",
                    "cache_hit": False,
                    "row_count": 1,
                },
            )
            event("result.ready", {"artifact_id": dataset_id})
            event("tool.started", {"task_id": "c_sales", "tool": "build_chart"})
            if status == "partial":
                tool("c_sales", "build_chart", "failed", "CHART_BUILD_FAILED")
                event("warning", {"code": "CHART_BUILD_FAILED"})
            else:
                tool("c_sales", "build_chart", "succeeded")
                figure_id = artifact(
                    "c_sales:figure",
                    "FIGURE",
                    {
                        "data": [
                            {
                                "type": "bar",
                                "x": ["2026-08"],
                                "y": [120],
                                "name": "销售额（元）",
                            }
                        ],
                        "layout": {
                            "title": {"text": "固定测试商品销售额"},
                            "yaxis": {"title": {"text": "人民币元"}},
                        },
                    },
                    [dataset_id],
                )
                event("tool.completed", {"task_id": "c_sales", "tool": "build_chart"})
                event("chart.ready", {"artifact_id": figure_id})
            text = "测试商品 2026 年 8 月已完成订单销售额为 120.00 元，订单数 1，毛利 50.00 元。"
            if status == "partial":
                text += "图表生成失败，保留数值结果。"
            answer_id = artifact(
                "final:answer",
                "ANSWER",
                {"text": text, "facts": totals, "source_dataset_ids": [dataset_id]},
                [dataset_id],
            )
            message("assistant", "answer", "final-answer", text)
            event(f"run.{status}", {"answer_artifact_id": answer_id})
        elif case == "awaiting_input":
            message(
                "assistant", "clarification", "clarify:1", "请补充要分析的日期范围。"
            )
            event(
                "clarification.required",
                {"missing_fields": ["date_range"], "fixture_without_checkpoint": True},
            )
        elif case == "interrupted":
            event("tool.started", {"task_id": "q_current", "tool": "query_sales"})
            tool("q_current", "query_sales", "interrupted", "PROCESS_RESTART")
            event(
                "run.interrupted",
                {"code": "PROCESS_RESTART", "fixture_without_checkpoint": True},
            )
        elif case == "forbidden":
            message(
                "assistant",
                "error",
                "final-error",
                "当前账号只能查看本人销售记录，不能查询华北全体人员。",
            )
            event("run.failed", {"code": "SCOPE_FORBIDDEN"})
        else:
            event("run.cancelled", {"reason": "用户在排队阶段取消"})
        if not terminal:
            session["active_run_id"] = run_id
        saved_at = tick()
        run["updated_at"] = session["updated_at"] = saved_at
        run["finished_at"] = saved_at if terminal else None
        run["active_elapsed_ms"] = 0 if case == "cancelled" else tick_count * 100
        manifest[case] = {
            "username": username,
            "session_id": session_id,
            "run_id": run_id,
            "status": status,
        }

    for case, username, status in cases:
        build_case(case, username, status)
    return manifest


def build_dataset(
    orders_per_month: int = 320, seed: int = 20260922
) -> tuple[dict, dict]:
    if not 1 <= orders_per_month <= 5000:
        raise ValueError("orders_per_month must be between 1 and 5000")
    data = {name: [] for name in BUSINESS_TABLES + APP_TABLES}
    make_business(data, orders_per_month, seed)
    users = make_accounts(data)
    cases = make_application(data, users)
    manifest = {
        "reference_date": REFERENCE_DATE.isoformat(),
        "application_clock_utc": STAMP.isoformat().replace("+00:00", "Z"),
        "available_from": AVAILABLE_FROM.isoformat(),
        "complete_through": COMPLETE_THROUGH.isoformat(),
        "seed": seed,
        "orders_per_month": orders_per_month,
        "data_version": DATA_VERSION,
        "row_counts": {name: len(rows) for name, rows in data.items()},
        "order_status_counts": dict(
            sorted(Counter(row["status"] for row in data["sales_order"]).items())
        ),
        "all_completed": sales_totals(data["sales_order"]),
        "monthly_totals": {
            month.strftime("%Y-%m"): sales_totals(
                data["sales_order"], month=month.strftime("%Y-%m")
            )
            for month in months()
        },
        "fixed_assertions": {
            "rep_10_product_12_2026_08": sales_totals(
                data["sales_order"], rep_id=10, product_id=12, month="2026-08"
            ),
            "yoy_baseline": "100.00",
            "yoy_growth_ratio": "0.20",
            "mom_baseline": "80.00",
            "mom_growth_ratio": "0.50",
            "zero_baseline": {
                "product_id": 9,
                "2026-07": "0.00",
                "2026-08": "500.00",
                "growth_ratio": None,
            },
            "decimal_product_13_2026_08": sales_totals(
                data["sales_order"], product_id=13, month="2026-08"
            ),
            "loss_product_14_2026_08": sales_totals(
                data["sales_order"], product_id=14, month="2026-08"
            ),
            "empty_region_id": 5,
            "unsold_product_id": 11,
            "unavailable_baseline": "2024-01",
            "leap_day": "2024-02-29",
        },
        "accounts": [
            {
                "username": user["username"],
                "id": user["id"],
                "role": user["role"],
                "rep_id": user["rep_id"],
                "active": user["active"],
                "permission_version": user["permission_version"],
            }
            for user in users.values()
        ],
        "application_cases": cases,
        "notes": [
            "Synthetic fixtures, not measured execution traces.",
            "No Redis checkpoints are generated.",
            "Freeze the application clock at application_clock_utc when testing expiry.",
        ],
    }
    return data, manifest


def sql_literal(value: object) -> str:
    """Rendered scripts explicitly enable NO_BACKSLASH_ESCAPES."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("SQL timestamps require an explicit timezone")
        value = (
            value.astimezone(UTC)
            .replace(tzinfo=None)
            .isoformat(sep=" ", timespec="milliseconds")
        )
    elif isinstance(value, date):
        value = value.isoformat()
    elif isinstance(value, (dict, list)):
        value = canonical(value)
    return "'" + str(value).replace("'", "''") + "'"


def qualified(
    table: str,
    business_db: str = BUSINESS_DB,
    app_db: str = APP_DB,
) -> str:
    return f"`{business_db if table in BUSINESS_TABLES else app_db}`.`{table}`"


def insert_statements(
    table: str,
    rows: list[dict],
    business_db: str = BUSINESS_DB,
    app_db: str = APP_DB,
) -> list[str]:
    if not rows:
        return []
    columns = list(rows[0])
    statements = []
    for start in range(0, len(rows), 200):
        values = []
        for row in rows[start : start + 200]:
            # Break the session/run cycle; active pointers are populated after run insertion.
            values.append(
                "("
                + ", ".join(
                    sql_literal(
                        None
                        if table == "chat_session" and column == "active_run_id"
                        else row[column]
                    )
                    for column in columns
                )
                + ")"
            )
        statements.append(
            f"INSERT INTO {qualified(table, business_db, app_db)} ("
            + ", ".join(f"`{column}`" for column in columns)
            + ") VALUES\n"
            + ",\n".join(values)
            + ";"
        )
    return statements


def render_seed(
    data: dict,
    business_db: str = BUSINESS_DB,
    app_db: str = APP_DB,
    profile: str = "demo",
) -> str:
    if (business_db, app_db) not in {
        ("sales_data_demo", "agent_app_demo"),
        ("sales_data", "agent_app"),
    }:
        raise ValueError("Only the reviewed demo or formal schema pair is allowed")
    is_demo = profile == "demo"
    if profile not in {"demo", "formal"}:
        raise ValueError("profile must be 'demo' or 'formal'")
    procedure = "seed_salesmind_demo_v1" if is_demo else "seed_salesmind_formal_v1"
    label = "Demo" if is_demo else "Formal"
    table_order = (
        [name for name in BUSINESS_TABLES if name != "dataset_version"]
        + APP_TABLES
        + ["dataset_version"]
    )
    statements = []
    for table in table_order:
        statements.extend(insert_statements(table, data[table], business_db, app_db))
    for session in data["chat_session"]:
        if session["active_run_id"]:
            statements.append(
                f"UPDATE {qualified('chat_session', business_db, app_db)} SET active_run_id = {sql_literal(session['active_run_id'])}, updated_at = {sql_literal(session['updated_at'])} WHERE id = {sql_literal(session['id'])};"
            )
    nonempty = "\n        OR ".join(
        f"EXISTS (SELECT 1 FROM {qualified(table, business_db, app_db)} LIMIT 1)" for table in table_order
    )
    body = "\n\n".join(statements)
    requires = (
        "Requires docs/sql/001_sales_data.sql and docs/sql/002_agent_app.sql first."
        if not is_demo
        else "Requires 000_create_demo.sql first."
    )
    target_note = (
        "Target: sales_data and agent_app."
        if not is_demo
        else "Synthetic data; demo schemas ONLY."
    )
    return f"""-- Generated by scripts/seed_demo.py. {target_note}
-- {requires} Run with a migration account on the intended instance.
-- A recognized seed is a no-op; it never resets test mutations or refreshes changed fixtures.
-- One transaction covers both schemas. Any INSERT/constraint failure rolls it back.
SET NAMES utf8mb4;
-- These settings are session-local; reconnecting restores the server defaults.
SET SESSION sql_mode = 'STRICT_ALL_TABLES,ONLY_FULL_GROUP_BY,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION,NO_BACKSLASH_ESCAPES';
SET time_zone = '+00:00';
USE `{app_db}`;
DELIMITER $$
CREATE PROCEDURE {procedure}()
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;
    START TRANSACTION;
    IF EXISTS (SELECT 1 FROM `{business_db}`.dataset_version
               WHERE id = 1 AND version = {DATA_VERSION}
               AND refreshed_at = '2026-09-22 06:00:00.000'
               AND available_from = '2024-02-01' AND complete_through = '2026-09-21') THEN
        COMMIT;
        SELECT '{label} already initialized: no rows changed; not a reset or content verification.' AS seed_status;
    ELSE
        IF {nonempty} THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Refusing seed: target tables must be empty or carry this seed marker.';
        END IF;

{body}

        COMMIT;
        SELECT 'Demo data imported.' AS seed_status;
    END IF;
END$$
DELIMITER ;
CALL {procedure}();
DROP PROCEDURE {procedure};
"""


def render_ddl() -> str:
    parts = [
        "-- Generated demo copies of the reviewed schema. Use a NEW test environment.\n"
    ]
    for filename, original, target in [
        ("001_sales_data.sql", "sales_data", BUSINESS_DB),
        ("002_agent_app.sql", "agent_app", APP_DB),
    ]:
        source = (ROOT / "docs" / "sql" / filename).read_text(encoding="utf-8")
        for keyword in ["CREATE DATABASE", "USE"]:
            old = f"{keyword} {original}"
            if source.count(old) != 1:
                raise ValueError(f"Unexpected schema declaration in {filename}: {old}")
            source = source.replace(old, f"{keyword} {target}", 1)
        parts.append(source)
    return "\n".join(parts)


def render_verification(
    data: dict,
    business_db: str = BUSINESS_DB,
    app_db: str = APP_DB,
) -> str:
    counts = (
        "\nUNION ALL\n".join(
            f"SELECT '{table}' AS table_name, COUNT(*) AS actual_count, {len(rows)} AS expected_count FROM {qualified(table, business_db, app_db)}"
            for table, rows in data.items()
        )
        + ";\n"
    )
    return f"""-- Read-only checks. Row counts apply immediately after the initial import.
{counts}

-- Expected: 2025-08 = 100.00; 2026-07 = 80.00; 2026-08 = 120.00.
SELECT DATE_FORMAT(order_date, '%Y-%m') AS month, SUM(amount) AS sales_amount,
       SUM(amount - cost) AS gross_profit, COUNT(*) AS order_count
FROM `{business_db}`.sales_order
WHERE rep_id = 10 AND product_id = 12 AND status = 'COMPLETED'
GROUP BY DATE_FORMAT(order_date, '%Y-%m') ORDER BY month;

-- Expected: July 0.00; August 500.00. Growth ratio must be NULL, not infinity.
SELECT DATE_FORMAT(order_date, '%Y-%m') AS month, SUM(amount) AS sales_amount
FROM `{business_db}`.sales_order WHERE product_id = 9 AND status = 'COMPLETED'
GROUP BY DATE_FORMAT(order_date, '%Y-%m') ORDER BY month;

-- Expected: historical region 1 and then 2; current rep region is 2 for both rows.
SELECT o.order_no, o.order_date, o.region_id AS order_region, r.region_id AS current_region
FROM `{business_db}`.sales_order o JOIN `{business_db}`.sales_rep r ON r.id = o.rep_id
WHERE o.order_no IN ('DEMO-BEFORE-TRANSFER', 'DEMO-AFTER-TRANSFER') ORDER BY o.order_date;

-- Expected zero rows: session/user mismatches, invalid active pointer or wrong sequence counters.
SELECT r.id FROM `{app_db}`.analysis_run r JOIN `{app_db}`.chat_session s ON s.id = r.session_id
WHERE r.owner_id <> s.owner_id
   OR r.last_event_seq <> (SELECT COALESCE(MAX(e.seq), 0) FROM `{app_db}`.run_event e WHERE e.run_id = r.id)
   OR r.last_message_seq <> (SELECT COALESCE(MAX(m.seq), 0) FROM `{app_db}`.chat_message m WHERE m.run_id = r.id);
SELECT s.id FROM `{app_db}`.chat_session s LEFT JOIN `{app_db}`.analysis_run r ON r.id = s.active_run_id
WHERE s.active_run_id IS NOT NULL AND (r.id IS NULL OR r.session_id <> s.id OR r.owner_id <> s.owner_id
      OR r.status IN ('completed', 'partial', 'failed', 'cancelled'));
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders-per-month", type=int, default=320)
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--profile", choices=("demo", "formal"), default="demo")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir is None:
        args.output_dir = ROOT / "docs" / "sql" / args.profile
    data, manifest = build_dataset(args.orders_per_month, args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.profile == "demo":
        files = {
            "000_create_demo.sql": render_ddl(),
            "003_seed_demo.sql": render_seed(data),
            "004_verify_demo.sql": render_verification(data),
            "expected_results.json": json.dumps(manifest, ensure_ascii=False, indent=2)
            + "\n",
        }
    else:
        files = {
            "001_import_formal.sql": render_seed(
                data, "sales_data", "agent_app", profile="formal"
            ),
            "002_verify_formal.sql": render_verification(
                data, "sales_data", "agent_app"
            ),
        }
    for filename, content in files.items():
        (args.output_dir / filename).write_text(content, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {"output_dir": str(args.output_dir), "row_counts": manifest["row_counts"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
