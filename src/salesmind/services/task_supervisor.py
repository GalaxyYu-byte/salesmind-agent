"""lifespan 管理的有界任务执行器；SSE 订阅不拥有任务生命周期。"""

import asyncio
import logging
import time

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from salesmind.core.config import Settings
from salesmind.db.entities import AppUser
from salesmind.db.session import Database
from salesmind.services.conversation_schemas import AnalysisSpec, RunCreate, RunResume
from salesmind.services.conversations import TERMINAL, Conversations
from salesmind.services.intent import (
    IntentError,
    IntentParser,
    load_intent_catalog,
    normalize_intent,
)
from salesmind.services.sales_overview import SalesQueryError, query_sales_overview
from salesmind.services.workflow import WorkflowContext, build_workflow

logger = logging.getLogger(__name__)


class TaskSupervisor:
    def __init__(self, database: Database, settings: Settings, *, query_cache=None):
        self.database, self.settings = database, settings
        self.query_cache = query_cache
        self.store = Conversations(database)
        self.intent_parser = IntentParser(settings)
        self.workflow = build_workflow()
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, str] = {}
        self.stop_targets: dict[str, str] = {}
        self.slots = asyncio.Semaphore(settings.run_concurrency)
        self.start_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.started = False
        self.closing = False

    async def ensure_started(self):
        # 按需恢复，数据库不可用时仍允许进程存活检查和登录错误正常返回。
        async with self.start_lock:
            if self.closing:
                raise HTTPException(503, "服务正在停止，请稍后重试")
            if not self.database.app_engine or not self.database.sales_engine:
                raise HTTPException(503, "数据库尚未配置")
            if not self.started:
                await self.store.recover()
                self.started = True

    def capacity(self) -> bool:
        return not self.closing and len(self.tasks) < self.settings.run_queue_limit

    def launch(self, owner: str, rid: str):
        task = asyncio.create_task(self.execute(owner, rid), name=f"analysis:{rid}")
        self.tasks[rid] = task
        self.owners[rid] = owner

        def finished(done):
            if self.tasks.get(rid) is done:
                self.tasks.pop(rid, None)
                self.owners.pop(rid, None)
                self.stop_targets.pop(rid, None)
            if not done.cancelled() and done.exception() is not None:
                # 不输出数据库异常文本、绑定参数或配置凭据。
                logger.error("Analysis task %s could not persist its final state", rid)

        task.add_done_callback(finished)

    async def submit(self, user: AppUser, sid: str, body: RunCreate, key: str) -> dict:
        result, should_launch = await self.store.create_run(
            user, sid, body, key, self.capacity
        )
        if should_launch:
            self.launch(user.id, result["id"])
        return result

    async def resume(self, user: AppUser, rid: str, body: RunResume) -> dict:
        # 检查内存中的执行状态前先校验归属，避免通过 409 泄露他人的任务存在。
        await self.store.get_run(user.id, rid)
        if rid in self.tasks and not self.tasks[rid].done():
            raise HTTPException(409, "旧执行尚未停止，请稍后继续")
        result = await self.store.resume(user, rid, body, self.capacity)
        self.launch(user.id, rid)
        return result

    async def stop(self, owner: str, rid: str, target="cancelled") -> dict:
        async with self.control_lock:
            return await self._stop(owner, rid, target)

    async def _stop(self, owner: str, rid: str, target: str) -> dict:
        current = await self.store.get_run(owner, rid)
        if current["status"] in TERMINAL:
            return current
        if target == "interrupted":
            if current["status"] == "interrupted":
                return current
            if current["status"] == "awaiting_input":
                raise HTTPException(409, "等待补充的任务无需暂停，请补充或取消")
        result = await self.store.request_stop(owner, rid)
        if result["status"] in TERMINAL:
            return await self.store.get_run(owner, rid)
        self.stop_targets[rid] = target
        task = self.tasks.get(rid)
        if task is not None and not task.done():
            task.cancel()
            # shield 保证浏览器断开取消请求也不会打断执行器的状态收尾。
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    raise
        await self.store.finish_stop(owner, rid, target, attempt=result["attempt"])
        return await self.store.get_run(owner, rid)

    async def execute(self, owner: str, rid: str):
        started = None
        attempt = None
        try:
            async with self.slots:
                run = await self.store.begin_attempt(owner, rid)
                if run is None:
                    return
                started, attempt = time.monotonic(), run["attempt"]
                remaining = self.settings.run_timeout - run["active_elapsed_ms"] / 1000
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    overview = await self.store.restore_query(owner, rid, attempt)
                    state = {"request": run["request"], "proceed": True}
                    if overview is not None:
                        state.update(
                            entry_node="analyze",
                            analysis=AnalysisSpec.model_validate(
                                run["request"]["analysis"]
                            ),
                            overview=overview,
                        )

                    async def resolve():
                        self.intent_parser.check_configured()
                        scope = await self.store.run_scope(owner, rid)
                        scope.require_data()
                        async with asyncio.timeout(self.settings.sales_query_timeout):
                            catalog = await load_intent_catalog(self.database, scope)
                        inputs = await self.store.intent_input(
                            owner, rid, attempt=attempt
                        )
                        if inputs is None:
                            raise asyncio.CancelledError
                        catalog.require_visible_regions(
                            inputs["question"], *inputs["replies"]
                        )
                        catalog.require_visible_products(
                            inputs["question"], *inputs["replies"]
                        )
                        raw_previous = inputs.pop("previous_analysis")
                        previous = (
                            AnalysisSpec.model_validate(raw_previous)
                            if raw_previous
                            else None
                        )
                        model_previous = None
                        if previous:
                            model_previous = previous.model_dump(
                                mode="json", exclude={"scope", "product_ids"}
                            )
                            model_previous["months"] = previous.scope.months
                            model_previous["region_name"] = next(
                                (
                                    r.name
                                    for r in catalog.regions
                                    if r.id == previous.scope.region
                                ),
                                None,
                            )
                            model_previous["region_all"] = previous.scope.region == 0
                            model_previous["product_names"] = [
                                product.name
                                for product in catalog.products
                                if product.id in previous.product_ids
                            ]
                        draft = await self.intent_parser.parse(
                            **inputs,
                            previous_analysis=model_previous,
                            allowed_regions=tuple(r.name for r in catalog.regions),
                            allowed_products=tuple(p.name for p in catalog.products),
                        )
                        if inputs["replies"] and previous is None and draft.follow_up:
                            # 同一任务的补充回答仍解析原问题与累计回答，不能误当成跨任务继承。
                            draft = draft.model_copy(update={"follow_up": False})
                        decision = normalize_intent(
                            draft, inputs["reference_date"], catalog, previous
                        )
                        scope = await self.store.run_scope(owner, rid)
                        if decision.kind == "ready":
                            scope.require_analysis(decision.analysis)
                        return decision

                    async def query(spec):
                        scope = await self.store.run_scope(owner, rid)
                        scope.require_analysis(spec)
                        async with self.database.sales_session() as sales:
                            async with asyncio.timeout(
                                self.settings.sales_query_timeout
                            ):
                                if self.query_cache is not None:
                                    return await self.query_cache.query(
                                        sales,
                                        spec.overview_query(),
                                        scope,
                                        query_sales_overview,
                                    )
                                return await query_sales_overview(
                                    sales, spec.overview_query(), scope
                                )

                    await self.workflow.ainvoke(
                        state,
                        config={"recursion_limit": 20},
                        context=WorkflowContext(
                            owner,
                            rid,
                            self.store,
                            resolve,
                            query,
                            self.settings.artifact_retention_days,
                            attempt,
                        ),
                    )
        except asyncio.CancelledError:
            await self.store.finish_stop(
                owner,
                rid,
                self.stop_targets.get(rid, "interrupted"),
                attempt=attempt,
            )
        except TimeoutError:
            await self.store.fail(
                owner,
                rid,
                "timeout",
                "分析或查询超时，请缩小范围后重试。",
                attempt=attempt,
            )
        except SalesQueryError as error:
            await self.store.fail(
                owner, rid, "invalid_scope", str(error), attempt=attempt
            )
        except IntentError as error:
            await self.store.fail(owner, rid, error.code, str(error), attempt=attempt)
        except HTTPException as error:
            await self.store.fail(
                owner,
                rid,
                "permission_denied" if error.status_code == 403 else "access_changed",
                str(error.detail),
                attempt=attempt,
            )
        except SQLAlchemyError:
            await self.store.fail(
                owner,
                rid,
                "database_unavailable",
                "数据服务暂时不可用，请稍后重试。",
                attempt=attempt,
            )
        except Exception:  # noqa: BLE001 -- 托管任务必须写入安全失败终态，不向客户端泄露异常。
            await self.store.fail(
                owner,
                rid,
                "execution_failed",
                "任务执行失败，请稍后重试。",
                attempt=attempt,
            )
        finally:
            if started is not None:
                await self.store.record_elapsed(
                    owner, rid, attempt, int((time.monotonic() - started) * 1000)
                )

    async def close(self):
        self.closing = True
        active = [(rid, self.owners[rid]) for rid in list(self.tasks)]
        if not active:
            await self.intent_parser.close()
            return
        try:
            async with asyncio.timeout(self.settings.run_shutdown_seconds):
                await asyncio.gather(
                    *(self.stop(owner, rid, "interrupted") for rid, owner in active)
                )
        except (TimeoutError, SQLAlchemyError, HTTPException):
            logger.error(
                "Task shutdown could not finish persistence; restart recovery is required"
            )
        finally:
            tasks = list(self.tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.wait(tasks, timeout=1)
            await self.intent_parser.close()
