"""LangGraph 有界流程；持久化和恢复由会话任务层管理。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from salesmind.services.analysis import calculate, rank, visualize
from salesmind.services.conversation_schemas import AnalysisSpec, RunCreate
from salesmind.services.intent import IntentDecision


class WorkflowState(TypedDict, total=False):
    request: dict
    entry_node: str
    decision: IntentDecision
    analysis: AnalysisSpec
    overview: Any
    rows: list[dict]
    ranked: dict
    result: dict
    proceed: bool


@dataclass
class WorkflowContext:
    owner: str
    rid: str
    store: Any
    resolve: Callable
    query: Callable
    retention: int
    attempt: int


NAMES = {
    "understand": "理解问题与校验",
    "plan": "组织执行步骤",
    "query": "查询数据",
    "analyze": "计算同比",
    "rank": "排序与筛选",
    "visualize": "生成图表",
    "result": "保存结果",
}


def build_workflow():
    graph = StateGraph(WorkflowState, context_schema=WorkflowContext)

    async def understand(state, runtime: Runtime[WorkflowContext]):
        c = runtime.context
        if not await c.store.stage(c.owner, c.rid, "understand", attempt=c.attempt):
            return {"proceed": False}
        body = RunCreate.model_validate(state["request"])
        decision = (
            await c.resolve()
            if body.mode == "auto"
            else IntentDecision("ready", body.analysis)
        )
        if decision.kind == "ready":
            if body.mode == "auto" and not await c.store.apply_intent(
                c.owner, c.rid, decision, attempt=c.attempt
            ):
                return {"proceed": False}
            ok = await c.store.stage(
                c.owner, c.rid, "understand", completed=True, attempt=c.attempt
            )
            return {"decision": decision, "analysis": decision.analysis, "proceed": ok}
        return {"decision": decision, "proceed": True}

    async def clarify(state, runtime: Runtime[WorkflowContext]):
        c = runtime.context
        await c.store.apply_intent(c.owner, c.rid, state["decision"], attempt=c.attempt)
        return {"proceed": False}

    async def unsupported(state, runtime: Runtime[WorkflowContext]):
        c = runtime.context
        await c.store.fail(
            c.owner,
            c.rid,
            "unsupported",
            state["decision"].message,
            metric_resolution=state["decision"].metric_resolution,
            attempt=c.attempt,
        )
        return {"proceed": False}

    async def plan(state, runtime: Runtime[WorkflowContext]):
        c = runtime.context
        spec = state["analysis"]
        ids = ["understand", "plan", "query", "analyze"]
        if spec.view in {"regions", "products"}:
            ids.append("rank")
        ids += ["visualize", "result"]
        steps = [
            {
                "id": node,
                "label": "检测历史窗口异常"
                if node == "analyze" and spec.goal == "anomalies"
                else "计算指标与同比"
                if node == "analyze" and spec.comparison == "yoy"
                else "计算指标与环比"
                if node == "analyze" and spec.comparison == "mom"
                else "计算指标"
                if node == "analyze"
                else NAMES[node],
            }
            for node in ids
        ]
        ok = await c.store.plan(c.owner, c.rid, steps, attempt=c.attempt)
        if ok:
            ok = await c.store.stage(c.owner, c.rid, "plan", attempt=c.attempt)
        if ok:
            ok = await c.store.stage(
                c.owner, c.rid, "plan", completed=True, attempt=c.attempt
            )
        return {"proceed": ok}

    async def perform(node, state, runtime):
        c = runtime.context
        if not await c.store.stage(c.owner, c.rid, node, attempt=c.attempt):
            return {"proceed": False}
        spec = state["analysis"]
        if node == "query":
            output = {"overview": await c.query(spec)}
            # 查询快照与完成事件一起提交，恢复时才能可靠地跳过查询。
            output["proceed"] = await c.store.checkpoint_query(
                c.owner, c.rid, c.attempt, output["overview"], c.retention
            )
            return output
        elif node == "analyze":
            output = {"rows": calculate(spec, state["overview"])}
        elif node == "rank":
            output = {"ranked": rank(spec, state["rows"])}
        elif node == "visualize":
            output = {
                "result": visualize(
                    spec,
                    state.get("ranked") or rank(spec, state["rows"]),
                    state["overview"],
                )
            }
        else:
            await c.store.complete(
                c.owner,
                c.rid,
                state["overview"],
                c.retention,
                state["result"],
                attempt=c.attempt,
            )
            return {"proceed": False}
        output["proceed"] = await c.store.stage(
            c.owner, c.rid, node, completed=True, attempt=c.attempt
        )
        return output

    graph.add_node("understand", understand)
    graph.add_node("clarify", clarify)
    graph.add_node("unsupported", unsupported)
    graph.add_node("plan", plan)
    for node in ["query", "analyze", "rank", "visualize", "result"]:
        # 默认参数捕获节点名，避免循环闭包引用最后一个节点。
        async def action(state, runtime: Runtime[WorkflowContext], node=node):
            return await perform(node, state, runtime)

        graph.add_node(node, action)
    graph.add_conditional_edges(
        START,
        lambda state: state.get("entry_node", "understand"),
        {"understand": "understand", "analyze": "analyze"},
    )
    graph.add_conditional_edges(
        "understand",
        lambda s: (
            END
            if not s["proceed"]
            else "plan"
            if s["decision"].kind == "ready"
            else s["decision"].kind
        ),
        {END: END, "plan": "plan", "clarify": "clarify", "unsupported": "unsupported"},
    )
    graph.add_edge("clarify", END)
    graph.add_edge("unsupported", END)
    for origin, target in [
        ("plan", "query"),
        ("query", "analyze"),
        ("rank", "visualize"),
        ("visualize", "result"),
    ]:
        graph.add_conditional_edges(
            origin,
            lambda s, target=target: target if s["proceed"] else END,
            {target: target, END: END},
        )
    graph.add_conditional_edges(
        "analyze",
        lambda s: (
            END
            if not s["proceed"]
            else "rank"
            if s["analysis"].view in {"regions", "products"}
            else "visualize"
        ),
        {END: END, "rank": "rank", "visualize": "visualize"},
    )
    graph.add_edge("result", END)
    return graph.compile()
