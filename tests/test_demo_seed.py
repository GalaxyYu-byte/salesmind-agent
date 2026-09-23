"""Validate fixture relationships and independent financial expectations offline.

These checks do not replace running the generated scripts on MySQL 8.4.
"""

import importlib.util
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_seed", ROOT / "scripts" / "seed_demo.py"
)
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


@pytest.fixture(scope="module")
def fixture_data():
    return seed.build_dataset()


def test_all_columns_unique_keys_and_foreign_keys_match_actual_schema(fixture_data):
    data, _ = fixture_data
    for filename in ["001_sales_data.sql", "002_agent_app.sql"]:
        sql = (ROOT / "docs" / "sql" / filename).read_text(encoding="utf-8")
        foreign_keys = []
        for table, body in re.findall(
            r"^CREATE TABLE (\w+) \(\n(.*?)^\) ENGINE=", sql, re.MULTILINE | re.DOTALL
        ):
            columns = set(
                re.findall(
                    r"^    (\w+) (?:BIGINT|TINYINT|INT|SMALLINT|CHAR|VARCHAR|DECIMAL|DATE|DATETIME|JSON|TEXT|MEDIUMTEXT)\b",
                    body,
                    re.MULTILINE,
                )
            )
            assert data[table], f"No sample rows in {table}"
            for row in data[table]:
                assert set(row) == columns, (table, set(row) ^ columns)
            for unique in re.findall(
                r"(?:PRIMARY KEY|UNIQUE KEY \w+) \(([^)]+)\)", body
            ):
                names = unique.split(", ")
                values = [tuple(row[name] for name in names) for row in data[table]]
                non_null = [value for value in values if None not in value]
                assert len(non_null) == len(set(non_null)), (table, names)
            for source, parent, target in re.findall(
                r"FOREIGN KEY \(([^)]+)\) REFERENCES (\w+) \(([^)]+)\)", body
            ):
                foreign_keys.append((table, source, parent, target))
        foreign_keys.extend(
            re.findall(
                r"ALTER TABLE (\w+) ADD CONSTRAINT \w+\s+FOREIGN KEY \(([^)]+)\) REFERENCES (\w+) \(([^)]+)\)",
                sql,
            )
        )
        for table, source, parent, target in foreign_keys:
            parent_keys = {
                tuple(row[name] for name in target.split(", ")) for row in data[parent]
            }
            for row in data[table]:
                value = tuple(row[name] for name in source.split(", "))
                assert None in value or value in parent_keys, (table, source, value)


def test_money_and_excluded_statuses_have_independent_expected_values(fixture_data):
    data, manifest = fixture_data
    orders = data["sales_order"]
    for order in orders:
        assert order["quantity"] > 0
        assert order["amount"] == order["unit_price"] * order["quantity"]
        assert all(
            isinstance(order[key], Decimal) and order[key] >= 0
            for key in ["unit_price", "amount", "cost"]
        )
    current = seed.sales_totals(orders, rep_id=10, product_id=12, month="2026-08")
    assert current == {
        "sales_amount": "120.00",
        "cost": "70.00",
        "gross_profit": "50.00",
        "order_count": 1,
        "quantity": 1,
    }
    base_year = Decimal(
        seed.sales_totals(orders, product_id=12, month="2025-08")["sales_amount"]
    )
    base_month = Decimal(
        seed.sales_totals(orders, product_id=12, month="2026-07")["sales_amount"]
    )
    assert (Decimal(120) - base_year) / base_year == Decimal("0.2")
    assert (Decimal(120) - base_month) / base_month == Decimal("0.5")
    assert sum(
        order["amount"]
        for order in orders
        if order["product_id"] == 12
        and order["order_date"].strftime("%Y-%m") == "2026-08"
    ) == Decimal(1820)
    precision = seed.sales_totals(orders, product_id=13, month="2026-08")
    assert precision == {
        "sales_amount": "68.58",
        "cost": "33.11",
        "gross_profit": "35.47",
        "order_count": 3,
        "quantity": 7,
    }
    assert seed.sales_totals(orders, product_id=14)["gross_profit"] == "-20.00"
    assert manifest["fixed_assertions"]["rep_10_product_12_2026_08"] == current


def test_date_coverage_zero_and_missing_baselines_are_distinct(fixture_data):
    data, _ = fixture_data
    orders = data["sales_order"]
    assert len(orders) == 32 * 320 + 21
    assert {order["order_date"].strftime("%Y-%m") for order in orders} == {
        month.strftime("%Y-%m") for month in seed.months()
    }
    assert all(
        date(2024, 2, 1) <= order["order_date"] < date(2026, 9, 22) for order in orders
    )
    for boundary in [
        date(2024, 2, 29),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 9, 21),
    ]:
        assert any(order["order_date"] == boundary for order in orders)
    zero = seed.sales_totals(orders, product_id=9, month="2026-07")
    assert zero["sales_amount"] == "0.00" and zero["order_count"] == 1
    assert (
        seed.sales_totals(orders, product_id=9, month="2026-08")["sales_amount"]
        == "500.00"
    )
    assert date(2024, 1, 31) < data["dataset_version"][0]["available_from"]
    assert not any(
        order["region_id"] == 5 or order["product_id"] == 11 for order in orders
    )
    assert all(
        order["order_date"] >= date(2026, 8, 1)
        for order in orders
        if order["product_id"] == 10
    )


def test_historical_region_and_inactive_dimensions_remain_intact(fixture_data):
    data, _ = fixture_data
    named = {order["order_no"]: order for order in data["sales_order"]}
    assert data["sales_rep"][1]["region_id"] == 2
    assert named["DEMO-BEFORE-TRANSFER"]["region_id"] == 1
    assert named["DEMO-AFTER-TRANSFER"]["region_id"] == 2
    assert data["sales_rep"][7]["active"] == data["product"][7]["active"] == 0
    assert named["DEMO-INACTIVE-HISTORY"]["amount"] == Decimal("59.90")
    assert all(
        order["order_date"] < date(2026, 7, 1)
        for order in data["sales_order"]
        if order["rep_id"] == 8 or order["product_id"] == 8
    )


def test_account_scope_edge_cases_and_real_password_hashes(fixture_data):
    data, _ = fixture_data
    users = {user["username"]: user for user in data["app_user"]}
    scopes = data["user_region_scope"]
    assert {
        row["region_id"]
        for row in scopes
        if row["user_id"] == users["demo_manager"]["id"]
    } == {1, 2}
    assert not any(row["user_id"] == users["demo_no_region"]["id"] for row in scopes)
    assert users["demo_no_metrics"]["allowed_metrics_json"] == []
    assert users["demo_disabled"]["active"] == 0
    assert "gross_profit" not in users["demo_rep_east"]["allowed_metrics_json"]
    assert len({user["password_hash"] for user in users.values()}) == len(users)
    assert seed.verify_demo_password(
        seed.DEMO_PASSWORD, users["demo_director"]["password_hash"]
    )
    assert not seed.verify_demo_password(
        "wrong", users["demo_director"]["password_hash"]
    )
    for user in users.values():
        assert (
            len(user["password_hash"]) <= 255
            and seed.DEMO_PASSWORD not in user["password_hash"]
        )
        assert user["rep_id"] is None or any(
            rep["id"] == user["rep_id"] for rep in data["sales_rep"]
        )


def test_application_state_sequences_scope_and_artifact_lifetime(fixture_data):
    data, manifest = fixture_data
    terminal = {"completed", "partial", "failed", "cancelled"}
    sessions = {row["id"]: row for row in data["chat_session"]}
    runs = {row["id"]: row for row in data["analysis_run"]}
    artifacts = {row["id"]: row for row in data["result_artifact"]}
    for run in runs.values():
        assert (run["finished_at"] is not None) == (run["status"] in terminal)
        assert sessions[run["session_id"]]["active_run_id"] == (
            None if run["status"] in terminal else run["id"]
        )
        for table, counter in [
            ("run_event", "last_event_seq"),
            ("chat_message", "last_message_seq"),
        ]:
            rows = [row for row in data[table] if row["run_id"] == run["id"]]
            assert [row["seq"] for row in rows] == list(range(1, run[counter] + 1))
        assert run["tool_calls"] == sum(
            row["run_id"] == run["id"] for row in data["tool_execution"]
        )
    for artifact in artifacts.values():
        run = runs[artifact["run_id"]]
        assert (
            artifact["scope_hash"]
            == run["scope_hash"]
            == seed.digest(artifact["schema_json"]["scope"])
        )
        assert artifact["permission_version"] == run["permission_version"]
        assert 0 < artifact["byte_size"] <= 1048576
        assert artifact["byte_size"] == len(
            seed.canonical(artifact["schema_json"]).encode()
        ) + len(seed.canonical(artifact["payload_json"]).encode())
        assert artifact["expires_at"] - artifact["created_at"] == timedelta(days=7)
        for source_id in artifact["schema_json"]["source_artifact_ids"]:
            assert artifacts[source_id]["owner_id"] == artifact["owner_id"]
            assert artifacts[source_id]["created_at"] < artifact["created_at"]
        if artifact["run_id"] == manifest["application_cases"]["expired"]["run_id"]:
            assert artifact["expires_at"] < seed.STAMP
        else:
            assert artifact["expires_at"] > seed.STAMP
    revoked = next(
        user for user in data["app_user"] if user["username"] == "demo_revoked"
    )
    historical = runs[manifest["application_cases"]["permission_changed"]["run_id"]]
    assert historical["permission_version"] == 1 and revoked["permission_version"] == 2
    assert historical["scope_hash"] != seed.digest(seed.scope_for(revoked, [1]))


def test_generated_sql_uses_demo_targets_and_non_destructive_guard(fixture_data):
    data, _ = fixture_data
    ddl = seed.render_ddl()
    rendered = seed.render_seed(data)
    assert len(re.findall(r"^CREATE TABLE ", ddl, re.MULTILINE)) == 13
    assert re.findall(r"^CREATE DATABASE (\w+)", ddl, re.MULTILINE) == [
        "sales_data_demo",
        "agent_app_demo",
    ]
    assert (
        "START TRANSACTION" in rendered
        and "ROLLBACK;" in rendered
        and "RESIGNAL;" in rendered
    )
    assert "SIGNAL SQLSTATE '45000'" in rendered
    assert "Demo already initialized" in rendered
    assert not re.search(
        r"\b(?:DELETE FROM|TRUNCATE|REPLACE INTO|INSERT IGNORE|ON DUPLICATE KEY)\b",
        rendered,
    )
    assert set(re.findall(r"(?:INSERT INTO|UPDATE) `(\w+)`", rendered)) == {
        "sales_data_demo",
        "agent_app_demo",
    }
    assert seed.sql_literal("O'Brien\\test") == "'O''Brien\\test'"
    assert "NO_BACKSLASH_ESCAPES" in rendered
    assert "old_mode" not in rendered
    assert "old_zone" not in rendered
    assert seed.render_seed(data) == rendered


def test_formal_seed_targets_main_schemas_and_has_empty_table_guard(fixture_data):
    data, _ = fixture_data
    rendered = seed.render_seed(data, "sales_data", "agent_app", profile="formal")
    assert "USE `agent_app`;" in rendered
    assert "`sales_data`.`sales_order`" in rendered
    assert "`agent_app`.`app_user`" in rendered
    assert "sales_data_demo" not in rendered
    assert "agent_app_demo" not in rendered
    assert "SIGNAL SQLSTATE '45000'" in rendered
    assert "seed_salesmind_formal_v1" in rendered
    verification = seed.render_verification(data, "sales_data", "agent_app")
    assert "sales_data_demo" not in verification
    assert "agent_app_demo" not in verification


def test_business_data_reproduces_without_global_rng_state():
    first = {name: [] for name in seed.BUSINESS_TABLES}
    second = {name: [] for name in seed.BUSINESS_TABLES}
    seed.make_business(first, 2, 7)
    seed.make_business(second, 2, 7)
    assert first == second
    assert len(first["sales_order"]) == 32 * 2 + 21


@pytest.mark.parametrize("amount", [0, -1, 5001])
def test_invalid_volume_is_rejected(amount):
    with pytest.raises(ValueError):
        seed.build_dataset(orders_per_month=amount)
