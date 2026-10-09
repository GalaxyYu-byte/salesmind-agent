"""真实 API/持久化/LangGraph 的逐轮评测；销售查询使用 spy，不连接业务库。"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from intent_cases import CASES, CATALOG, REFERENCE
from test_conversations import (  # 复用隔离的 SQLite/Redis/API 测试环境。
    OWNER,
    RESULT,
    model_environment,
    session,
    submit,
)
from test_conversations import (
    environment as conversation_environment,
)

from salesmind.core.config import Settings
from salesmind.services.intent import IntentError
from salesmind.services.sales_overview import AccessInfo

evaluation_environment = conversation_environment


class EvaluationClock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 10, 8, 4, tzinfo=UTC)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


def differences(actual, expected, path=""):
    """比较预期指定的语义字段，允许等价模型输出中的其他默认字段。"""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return [
            issue
            for key, value in expected.items()
            for issue in differences(actual.get(key), value, f"{path}.{key}")
        ]
    if actual != expected:
        return [f"{path}: expected={expected!r}, actual={actual!r}"]
    return []


@pytest.fixture(scope="session")
def evaluation_report(request):
    live = request.config.getoption("--intent-live")
    rows = []
    yield rows
    output = request.config.getoption("--intent-report")
    if not output:
        return
    data = {
        "mode": "live" if live else "offline-gold-drafts",
        "reference_date": REFERENCE.isoformat(),
        "timezone": "Asia/Shanghai",
        "catalog": {
            "available_from": CATALOG.available_from.isoformat(),
            "complete_through": CATALOG.complete_through.isoformat(),
            "regions": [r.model_dump() for r in CATALOG.regions],
            "products": [p.model_dump() for p in CATALOG.products],
        },
        "model": Settings().llm_model if live else "mock",
        "sales_database": "isolated spy; no real sales query",
        "cases_total": len(CASES),
        "turns_total": sum(len(c["turns"]) for c in CASES),
        "cases_passed": sum(
            len([r for r in rows if r["case_id"] == c["id"]]) == len(c["turns"])
            and all(r["passed"] for r in rows if r["case_id"] == c["id"])
            for c in CASES
        ),
        "turns_passed": sum(r["passed"] for r in rows),
        "rows": rows,
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 意图解析评测结果",
        "",
        f"模式：{data['mode']}；模型：{data['model']}；固定参考日：{data['reference_date']}。",
        (
            f"通过：{data['cases_passed']}/{data['cases_total']} 组，"
            f"{data['turns_passed']}/{data['turns_total']} 轮。"
        ),
        "",
        "真实模式调用模型；离线模式只验证给定提取结果的校验与查询阻断，不能衡量自然语言准确率。",
        "两种模式都运行真实会话 API、SQLite 持久化与 LangGraph；销售聚合查询用 spy 统计次数。",
        "",
        "| 用例 | 轮次/输入 | 预期→实际状态 | 预期/实际查询次数 | 结果 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['turn']}：{row['text']} | "
            f"{row['expected']['status']} → {row['actual'].get('status')} | "
            f"{row['expected']['query_delta']}/{row['actual'].get('query_delta')} | "
            f"{'通过' if row['passed'] else '失败'} |"
        )
    for row in rows:
        if row["issues"]:
            lines.extend(["", f"## {row['case_id']} 第 {row['turn']} 轮偏差", ""])
            lines.extend(f"- {issue}" for issue in row["issues"])
    target.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_intent_evaluation(
    case, evaluation_environment, monkeypatch, request, evaluation_report
):
    client, supervisor, _, _, _ = evaluation_environment
    live = request.config.getoption("--intent-live")
    if live:
        settings = Settings()
        supervisor.intent_parser.settings = settings
        supervisor.intent_parser.check_configured()
        query = AsyncMock()
    else:
        _, query = await model_environment(
            evaluation_environment,
            monkeypatch,
            [t["gold_draft"] for t in case["turns"]],
        )
    monkeypatch.setattr("salesmind.services.conversations.datetime", EvaluationClock)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.load_intent_catalog",
        AsyncMock(return_value=CATALOG),
    )

    async def sales_result(db, params, scope):
        # 返回与请求范围相符的隔离样例，计算/图表也走真实图节点。
        result = RESULT.model_copy(deep=True)
        result.access = AccessInfo.model_validate(scope.public())
        result.comparison = params.comparison
        result.months = (
            [params.start_month]
            if params.start_month == params.end_month
            else ["2026-07", "2026-08"]
        )
        result.region_id = params.region_id
        result.dataset.regions = list(CATALOG.regions)
        result.dataset.products = list(CATALOG.products)
        for cell in result.dataset.cells:
            cell.region = params.region_id or 1
            cell.month = result.months[0]
        return result

    query.side_effect = sales_result
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    parsed = []
    original_parse = supervisor.intent_parser.parse

    async def capture_parse(**kwargs):
        entry = {
            "question": kwargs["question"],
            "replies": kwargs["replies"],
            "reference_date": kwargs["reference_date"].isoformat(),
            "previous_analysis": kwargs["previous_analysis"],
        }
        parsed.append(entry)
        try:
            value = await original_parse(**kwargs)
            entry["draft"] = value.model_dump(mode="json")
            return value
        except IntentError as error:
            entry["error_code"] = error.code
            raise

    monkeypatch.setattr(supervisor.intent_parser, "parse", capture_parse)
    sid = await session(client)
    rid, replies, root_question, completed_rid = None, [], None, None
    issues = []
    for index, turn in enumerate(case["turns"], 1):
        expected = turn["expected"]
        before_queries, before_parsed = query.await_count, len(parsed)
        if turn["action"] == "reply":
            replies.append(turn["text"])
            response = await client.post(
                f"/api/v1/runs/{rid}/resume", json={"reply": turn["text"]}
            )
        else:
            root_question, replies = turn["text"], []
            response = await submit(client, sid, {"question": turn["text"]})
        row = {
            "case_id": case["id"],
            "name": case["name"],
            "turn": index,
            "text": turn["text"],
            "expected": expected,
            "expected_draft": turn["gold_draft"],
            "actual": {},
            "issues": [],
            "passed": False,
        }
        evaluation_report.append(row)
        if response.status_code != 202:
            row["issues"] = [f"提交 HTTP {response.status_code}；后续轮次无法继续"]
            issues.extend(row["issues"])
            break
        new_rid = response.json()["id"]
        if turn["action"] == "reply" and new_rid != rid:
            row["issues"].append("澄清回复没有沿用同一 run")
        rid = new_rid
        task = supervisor.tasks.get(rid)
        if task:
            await asyncio.wait_for(
                asyncio.shield(task), supervisor.intent_parser.settings.llm_timeout + 10
            )
        current = (await client.get(f"/api/v1/runs/{rid}")).json()
        _, events = await supervisor.store.events(OWNER, rid, 0)
        starts = [e["payload"]["stage"] for e in events if e["type"] == "stage.started"]
        actual = {
            "status": current["status"],
            "analysis": current["request"].get("analysis"),
            "fields": current["clarification_fields"],
            "error_code": current["error_code"],
            "query_delta": query.await_count - before_queries,
            "inherited": current["inherited_fields"],
            "stages": starts,
            "context_run_id": current["context_run_id"],
            "metric_resolution": current["metric_resolution"],
        }
        if query.await_count > before_queries:
            actual["query_parameters"] = query.await_args.args[1].model_dump()
        row["actual"] = actual
        row["issues"] += differences(
            actual, {k: v for k, v in expected.items() if k != "inherited"}
        )
        if not set(expected["inherited"]).issubset(actual["inherited"]):
            row["issues"].append(
                f"继承字段缺失：{expected['inherited']}，实际 {actual['inherited']}"
            )
        if len(parsed) != before_parsed + 1:
            row["issues"].append("每轮必须恰好解析一次")
        else:
            row["model_input"] = parsed[-1]
            row["issues"] += differences(
                parsed[-1],
                {
                    "question": root_question,
                    "replies": replies,
                    "reference_date": REFERENCE.isoformat(),
                    "draft": turn["gold_draft"],
                },
            )
        if expected["query_delta"] == 0 and "query" in starts:
            row["issues"].append("应阻断时启动了 query 节点")
        if expected["analysis"]:
            scope = expected["analysis"]["scope"]
            row["issues"] += differences(
                actual.get("query_parameters"),
                {
                    "start_month": scope["months"][0],
                    "end_month": scope["months"][-1],
                    "region_id": scope["region"] or None,
                },
            )
        if turn["action"] == "question" and actual["context_run_id"] != completed_rid:
            row["issues"].append("上下文没有对应本会话上一轮完成任务")
        if current["status"] == "completed":
            completed_rid = rid
        row["passed"] = not row["issues"]
        issues.extend(f"第 {index} 轮 {issue}" for issue in row["issues"])
    assert not issues, "\n".join(issues)
