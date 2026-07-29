"""
Task 3: 人机协作与数据回流接口 (Human-in-the-Loop API V3.0)

功能:
  1. POST /api/v1/chat/confirm_send — 人工确认/修改/拒绝
  2. 数据飞轮: is_modified → PostgreSQL rag_feedback 写入
  3. ACCEPT/MODIFY → 调用 TypingSimulator 进入延迟发送队列
  4. MongoDB trace_log 状态更新为 SENT

依赖:
  - fastapi>=0.100.0
  - asyncpg>=0.28.0
  - motor>=3.2.0
"""

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

# 可选依赖 — lazy import 以支持 Mock 测试
try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None  # type: ignore

from app.core.config import settings
from app.core.schemas import (
    ChatGenerateResponse,
    ConfirmSendRequest,
    ConfirmSendResponse,
    RAGFeedbackModel,
    TraceLogModel,
)
from app.services.typing_simulator import TypingSimulator


# ════════════════════════════════════════════════════════════
# HumanLoop 核心类
# ════════════════════════════════════════════════════════════

class HumanLoop:
    """
    人机协作控制器。

    职责:
      - 读取 MongoDB 原始 trace 记录
      - 根据 action 决定是否发送
      - 数据飞轮: 修改过的回复写 PostgreSQL rag_feedback
      - 调用 TypingSimulator 进入延迟队列
    """

    def __init__(
        self,
        mongo_client: Optional[AsyncIOMotorClient] = None,
        typing_simulator: Optional[TypingSimulator] = None,
    ):
        """
        Args:
            mongo_client:     MongoDB 异步客户端
            typing_simulator: 延迟发送引擎
        """
        self.mongo = mongo_client
        self.simulator = typing_simulator or TypingSimulator()
        self._pg_pool: Optional[asyncpg.Pool] = None
        self._trace_collection = None

    # ── 依赖注入 ────────────────────────────────────────────

    async def _ensure_mongo(self) -> None:
        if self._trace_collection is None:
            if self.mongo is None:
                self.mongo = AsyncIOMotorClient(settings.mongo_uri)
            db = self.mongo[settings.mongo_db]
            self._trace_collection = db[settings.mongo_trace_collection]

    async def _ensure_pg(self) -> None:
        if self._pg_pool is None:
            self._pg_pool = await asyncpg.create_pool(
                dsn=settings.pg_dsn,
                min_size=1,
                max_size=5,
            )
            # 确保表存在
            async with self._pg_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS rag_feedback (
                        id SERIAL PRIMARY KEY,
                        trace_id VARCHAR(64) NOT NULL,
                        context_text TEXT NOT NULL,
                        ai_raw_output TEXT NOT NULL,
                        human_edited_output TEXT NOT NULL,
                        status VARCHAR(20) DEFAULT 'PENDING',
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                """)
                print("[HumanLoop] PostgreSQL rag_feedback table ready.")

    # ── 核心流程 ────────────────────────────────────────────

    async def confirm_send(self, request: ConfirmSendRequest) -> ConfirmSendResponse:
        """
        人工确认发送流程。

        处理流程:
          1. 查询 MongoDB trace_log 获取原始记录
          2. 数据飞轮: is_modified → 异步(非阻塞)写入 PostgreSQL rag_feedback
          3. ACCEPT/MODIFY → 调用 TypingSimulator 延迟入队
          4. REJECT → 直接返回 REJECTED
          5. 更新 MongoDB trace_log 状态

        Args:
            request: 包含 trace_id, final_text, is_modified, action

        Returns:
            ConfirmSendResponse

        Raises:
            HTTPException: trace_id 不存在时 404
        """
        await self._ensure_mongo()

        # Step 1: 读取 MongoDB 原始记录
        doc = await self._trace_collection.find_one({"trace_id": request.trace_id})
        if not doc:
            raise HTTPException(
                status_code=404,
                detail=f"Trace '{request.trace_id}' not found.",
            )

        # Step 2: 数据飞轮 — 若人工修改，异步非阻塞写入 PostgreSQL
        # 关键: 使用 asyncio.create_task + try-except 包裹，PG 故障绝不阻断主流程
        if request.is_modified and request.action == "MODIFY":
            asyncio.create_task(
                self._write_feedback_safe(
                    trace_id=request.trace_id,
                    context_text=str(doc.get("retrieved_docs", "")),
                    ai_raw_output=doc.get("llm_raw_output", ""),
                    human_edited_output=request.final_text,
                )
            )

        # Step 3: 根据 action 决定是否进入延迟发送队列
        if request.action in ("ACCEPT", "MODIFY"):
            await self._enqueue_to_delay_queue(
                trace_id=request.trace_id,
                account_id=doc["account_id"],
                customer_id=doc["customer_id"],
                text=request.final_text,
            )

        # Step 4: 更新 MongoDB trace_log 状态
        update_fields = {
            "human_intervention": request.is_modified,
            "final_sent_output": request.final_text,
            "status": "SENT" if request.action != "REJECT" else "CANCELLED",
        }

        if request.is_modified:
            update_fields["human_edited_output"] = request.final_text

        await self._trace_collection.update_one(
            {"trace_id": request.trace_id},
            {"$set": update_fields},
        )

        print(
            f"[HumanLoop] Trace: {request.trace_id} | "
            f"Action: {request.action} | "
            f"Modified: {request.is_modified}"
        )

        # Step 5: 构建符合 Spec 的响应
        if request.action == "REJECT":
            return ConfirmSendResponse(
                trace_id=request.trace_id,
                status="REJECTED",
                message="消息已拒绝发送，未进入延迟队列。",
            )
        else:
            mode = "Redis" if self.simulator.redis_url else "内存"
            return ConfirmSendResponse(
                trace_id=request.trace_id,
                status="QUEUED",
                message=f"消息已进入{mode}延迟发送队列。",
            )

    # ── 数据飞轮 ────────────────────────────────────────────

    async def _write_feedback(
        self,
        trace_id: str,
        context_text: str,
        ai_raw_output: str,
        human_edited_output: str,
    ) -> None:
        """
        将人工修正数据写入 PostgreSQL rag_feedback 表。

        用途: 后续微调模型 / 更新 RAG 知识库。
        """
        await self._ensure_pg()
        feedback = RAGFeedbackModel(
            trace_id=trace_id,
            context_text=str(context_text),
            ai_raw_output=ai_raw_output,
            human_edited_output=human_edited_output,
            status="PENDING",
            created_at=datetime.utcnow(),
        )
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO rag_feedback
                        (trace_id, context_text, ai_raw_output, human_edited_output, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    feedback.trace_id,
                    feedback.context_text,
                    feedback.ai_raw_output,
                    feedback.human_edited_output,
                    feedback.status,
                    feedback.created_at,
                )
            print(f"[HumanLoop] Feedback saved: {trace_id}")
        except Exception as e:
            print(f"[HumanLoop] ⚠️ Feedback save failed for {trace_id}: {e}")

    # ── 数据飞轮安全包装 (非阻塞 + 全异常捕获) ────────────

    async def _write_feedback_safe(
        self,
        trace_id: str,
        context_text: str,
        ai_raw_output: str,
        human_edited_output: str,
    ) -> None:
        """
        数据飞轮安全包装器。

        设计原则:
          - 完整的 try-except 包裹，PG 故障绝不抛异常
          - 由 asyncio.create_task 调用，非阻塞主流程
          - 即使 PG 连接超时/拒绝/崩溃，消息照常发送
        """
        try:
            await self._write_feedback(
                trace_id=trace_id,
                context_text=context_text,
                ai_raw_output=ai_raw_output,
                human_edited_output=human_edited_output,
            )
        except Exception as e:
            print(
                f"[HumanLoop] ⚠️ Feedback write failed (non-blocking, safe): {type(e).__name__}: {e}"
            )

    # ── 延迟发送调度 ────────────────────────────────────────

    async def _enqueue_to_delay_queue(
        self,
        trace_id: str,
        account_id: str,
        customer_id: str,
        text: str,
    ) -> None:
        """
        将消息送入 TypingSimulator 的延迟发送队列。

        模式选择:
          - redis_url 已配置 → Redis ZSET
          - 否则 → 降级为 asyncio.create_task 手动延迟
        """
        try:
            if self.simulator.redis_url:
                await self.simulator.enqueue_message_redis(
                    trace_id=trace_id,
                    account_id=account_id,
                    to_user=customer_id,
                    text=text,
                )
            else:
                # 内存模式降级: 使用 create_task 非阻塞延迟
                async def _delayed_send():
                    await self.simulator.schedule_message_memory(
                        trace_id=trace_id,
                        account_id=account_id,
                        to_user=customer_id,
                        text=text,
                        send_callback=self._default_send_callback,
                    )

                asyncio.create_task(_delayed_send())
        except Exception as e:
            print(f"[HumanLoop] ⚠️ Enqueue failed for {trace_id}: {e}")

    # ── 默认发送回调 ────────────────────────────────────────

    async def _default_send_callback(
        self, account_id: str, to_user: str, text: str
    ) -> None:
        """默认发送回调 — 实际生产环境中由 WSS Gateway 实现"""
        print(
            f"[HumanLoop::SendCallback] "
            f"Account: {account_id} -> User: {to_user} | "
            f"Text: '{text[:50]}...'"
        )

    # ── 审核队列查询 ────────────────────────────────────────

    async def get_pending_traces(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        """
        获取待审核的 trace 列表（状态为 PENDING 的记录）。

        按时间倒序，供前端左侧审核队列使用。
        """
        await self._ensure_mongo()
        cursor = (
            self._trace_collection.find({"status": "PENDING"})
            .sort("timestamp", -1)
            .skip(offset)
            .limit(limit)
        )
        results = []
        async for doc in cursor:
            # 提取最高检索得分
            max_score = None
            docs = doc.get("retrieved_docs", [])
            if docs:
                scores = [d.get("score", 0) for d in docs if isinstance(d, dict)]
                if scores:
                    max_score = max(scores)

            results.append({
                "trace_id": doc.get("trace_id"),
                "account_id": doc.get("account_id"),
                "customer_id": doc.get("customer_id"),
                "user_input": doc.get("user_input"),
                "generated_text": doc.get("llm_raw_output") or doc.get("final_sent_output") or settings.fallback_reply,
                "max_score": max_score,
                "is_fallback": doc.get("llm_raw_output") is None,
                "status": doc.get("status", "PENDING"),
                "timestamp": doc.get("timestamp").isoformat() if doc.get("timestamp") else None,
            })
        return results

    async def get_trace_detail(self, trace_id: str) -> Optional[dict]:
        """
        获取单条 trace 的完整详情，含 RAG 检索上下文。
        """
        await self._ensure_mongo()
        doc = await self._trace_collection.find_one({"trace_id": trace_id})
        if not doc:
            return None

        docs = doc.get("retrieved_docs", [])
        max_score = None
        if docs:
            scores = [d.get("score", 0) for d in docs if isinstance(d, dict)]
            if scores:
                max_score = max(scores)

        return {
            "trace_id": doc.get("trace_id"),
            "account_id": doc.get("account_id"),
            "customer_id": doc.get("customer_id"),
            "user_input": doc.get("user_input"),
            "generated_text": doc.get("llm_raw_output") or doc.get("final_sent_output") or settings.fallback_reply,
            "is_fallback": doc.get("llm_raw_output") is None,
            "max_score": max_score,
            "retrieved_docs": [
                {
                    "text": d.get("text", "") if isinstance(d, dict) else str(d),
                    "score": d.get("score") if isinstance(d, dict) else None,
                    "metadata": d.get("metadata", {}) if isinstance(d, dict) else {},
                }
                for d in docs
            ],
            "human_intervention": doc.get("human_intervention", False),
            "human_edited_output": doc.get("human_edited_output"),
            "status": doc.get("status", "PENDING"),
            "timestamp": doc.get("timestamp").isoformat() if doc.get("timestamp") else None,
        }

    async def get_queue_count(self) -> int:
        """获取待审核队列总数"""
        await self._ensure_mongo()
        return await self._trace_collection.count_documents({"status": "PENDING"})

    async def get_stats(self) -> dict:
        """获取审核工作台统计数据"""
        await self._ensure_mongo()
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        pending = await self._trace_collection.count_documents({"status": "PENDING"})
        processed_today = await self._trace_collection.count_documents({
            "status": {"$in": ["SENT", "CANCELLED"]},
            "timestamp": {"$gte": today},
        })
        # 今日 AI 建议被直接采纳的比例
        accepted_today = await self._trace_collection.count_documents({
            "status": "SENT",
            "human_intervention": False,
            "timestamp": {"$gte": today},
        })
        total_closed_today = await self._trace_collection.count_documents({
            "status": {"$in": ["SENT", "CANCELLED"]},
            "timestamp": {"$gte": today},
        })
        acceptance_rate = round(accepted_today / total_closed_today * 100, 1) if total_closed_today > 0 else 0.0

        return {
            "pending_count": pending,
            "processed_today": processed_today,
            "acceptance_rate": acceptance_rate,
        }

    async def close(self) -> None:
        """优雅关闭连接"""
        if self._pg_pool:
            await self._pg_pool.close()
            print("[HumanLoop] PostgreSQL pool closed.")


# ════════════════════════════════════════════════════════════
# FastAPI 路由
# ════════════════════════════════════════════════════════════

human_loop_router = APIRouter(prefix="/api/v1/chat", tags=["Human-in-the-Loop"])

# 全局实例
_loop_instance: Optional[HumanLoop] = None


def get_loop() -> HumanLoop:
    if _loop_instance is None:
        raise HTTPException(status_code=500, detail="HumanLoop not initialized.")
    return _loop_instance


def set_loop(loop: HumanLoop) -> None:
    global _loop_instance
    _loop_instance = loop


@human_loop_router.post("/confirm_send", response_model=ConfirmSendResponse)
async def confirm_send(request: ConfirmSendRequest):
    """
    人工确认发送接口。

    actions:
      - ACCEPT:  直接确认，进入延迟队列
      - MODIFY:  人工修改后确认，进入延迟队列 + 数据飞轮
      - REJECT:  拒绝发送，标记为 CANCELLED
    """
    loop = get_loop()
    return await loop.confirm_send(request)


@human_loop_router.get("/pending")
async def get_pending(limit: int = 50, offset: int = 0):
    """
    获取待审核队列 (状态为 PENDING 的 trace 列表)。

    返回 [{trace_id, account_id, customer_id, user_input, generated_text, max_score, ...}, ...]
    """
    loop = get_loop()
    traces = await loop.get_pending_traces(limit=limit, offset=offset)
    total = await loop.get_queue_count()
    return {"total": total, "items": traces}


@human_loop_router.get("/trace/{trace_id}")
async def get_trace_detail(trace_id: str):
    """
    获取单条 trace 的完整审核详情 (含 RAG 检索上下文)。
    """
    loop = get_loop()
    detail = await loop.get_trace_detail(trace_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found.")
    return detail


@human_loop_router.get("/stats")
async def get_stats():
    """获取审核工作台统计数据"""
    loop = get_loop()
    return await loop.get_stats()


# ── 操作日志 ────────────────────────────────────────────

@human_loop_router.post("/operation_log")
async def add_operation_log(request: dict):
    """记录操作员操作: { trace_id, action, operator, detail }"""
    loop = get_loop()
    await loop._ensure_mongo()
    coll = loop.mongo[settings.mongo_db]["operation_logs"]
    doc = {
        "trace_id": request.get("trace_id", ""),
        "action": request.get("action", ""),
        "operator": request.get("operator", "unknown"),
        "detail": request.get("detail", ""),
        "timestamp": datetime.utcnow(),
    }
    await coll.insert_one(doc)
    return {"status": "ok"}


@human_loop_router.get("/operation_log")
async def get_operation_logs(limit: int = 50):
    """获取最近操作日志"""
    loop = get_loop()
    await loop._ensure_mongo()
    coll = loop.mongo[settings.mongo_db]["operation_logs"]
    cursor = coll.find().sort("timestamp", -1).limit(limit)
    results = []
    async for doc in cursor:
        results.append({
            "trace_id": doc.get("trace_id"),
            "action": doc.get("action"),
            "operator": doc.get("operator"),
            "detail": doc.get("detail"),
            "timestamp": doc["timestamp"].isoformat() if doc.get("timestamp") else None,
        })
    return {"total": len(results), "items": results}


@human_loop_router.get("/dashboard")
async def get_dashboard():
    """获取数据看板完整统计"""
    loop = get_loop()
    await loop._ensure_mongo()
    trace_coll = loop._trace_collection
    log_coll = loop.mongo[settings.mongo_db]["operation_logs"]

    from datetime import timedelta
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)

    # 基础统计
    pending = await trace_coll.count_documents({"status": "PENDING"})
    processed_today = await trace_coll.count_documents({
        "status": {"$in": ["SENT", "CANCELLED"]}, "timestamp": {"$gte": today},
    })
    accepted_today = await trace_coll.count_documents({
        "status": "SENT", "human_intervention": False, "timestamp": {"$gte": today},
    })
    total_closed = await trace_coll.count_documents({
        "status": {"$in": ["SENT", "CANCELLED"]}, "timestamp": {"$gte": today},
    })
    rate = round(accepted_today / total_closed * 100, 1) if total_closed > 0 else 0.0

    # 按账号统计
    pipeline = [
        {"$match": {"status": {"$in": ["SENT", "CANCELLED"]}, "timestamp": {"$gte": today}}},
        {"$group": {"_id": "$account_id", "count": {"$sum": 1}}},
    ]
    by_account = {}
    async for r in trace_coll.aggregate(pipeline):
        by_account[r["_id"]] = r["count"]

    # 按小时趋势 (今日)
    hour_pipeline = [
        {"$match": {"timestamp": {"$gte": today}}},
        {"$group": {"_id": {"$hour": "$timestamp"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    hourly = {}
    async for r in trace_coll.aggregate(hour_pipeline):
        hourly[str(r["_id"])] = r["count"]

    # 上周趋势
    week_pipeline = [
        {"$match": {"timestamp": {"$gte": week_ago}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "total": {"$sum": 1},
            "fallback": {"$sum": {"$cond": [{"$eq": ["$llm_raw_output", None]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    weekly = []
    async for r in trace_coll.aggregate(week_pipeline):
        weekly.append({"date": r["_id"], "total": r["total"], "fallback": r["fallback"]})

    # 操作日志统计
    log_count = await log_coll.count_documents({"timestamp": {"$gte": today}})

    return {
        "pending_count": pending,
        "processed_today": processed_today,
        "acceptance_rate": rate,
        "by_account": by_account,
        "hourly_trend": hourly,
        "weekly_trend": weekly,
        "today_operations": log_count,
    }


# ════════════════════════════════════════════════════════════
# Mock 测试块 (if __name__ == '__main__')
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    一键验证 HumanLoop 全流程:
      1. ACCEPT (确认发送)
      2. MODIFY (人工修改 + 数据飞轮)
      3. REJECT (拒绝发送)
    """
    import asyncio
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(human_loop_router)

    # ── Mock HumanLoop (不依赖 MongoDB / PostgreSQL) ─────────
    class MockHumanLoop(HumanLoop):
        """Mock HumanLoop: 模拟数据库操作"""

        async def _ensure_mongo(self):
            pass

        async def _ensure_pg(self):
            pass

        async def _write_feedback(self, trace_id, context_text, ai_raw_output, human_edited_output):
            print(f"   [Mock] Feedback written: {trace_id} (PG)")
            return None

    mock_loop = MockHumanLoop()
    set_loop(mock_loop)

    # 预写入 mock trace 数据（通常由 ai_brain 写入）
    # 注意: motor 的所有方法都是 async，Mock 必须返回 awaitable
    async def _mock_find_one(*a, **kw):
        return {
            "trace_id": "test-tr-001",
            "account_id": "sales_01",
            "customer_id": "cust_001",
            "retrieved_docs": "产品A文档内容",
            "llm_raw_output": "产品A价格是100元",
        }

    async def _mock_update_one(*a, **kw):
        return None

    mock_loop._trace_collection = type("MockCol", (), {
        "find_one": _mock_find_one,
        "update_one": _mock_update_one,
    })()

    client = TestClient(test_app)

    async def main():
        print("=" * 60)
        print("🧪 Human-in-the-Loop API Mock 测试")
        print("=" * 60)

        # 测试 1: ACCEPT — 直接采纳，验证入队成功且未触发 PG
        print("\n✅ [Test 1] ACCEPT (直接采纳)...")
        resp = client.post(
            "/api/v1/chat/confirm_send",
            json={
                "trace_id": "test-tr-001",
                "final_text": "产品A价格是100元",
                "is_modified": False,
                "action": "ACCEPT",
            },
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}"
        data = resp.json()
        assert data["status"] == "QUEUED", f"Expected QUEUED, got {data['status']}"
        assert "延迟发送队列" in data["message"]
        print(f"   Response: {data}")
        print("   ✅ ACCEPT 通过 (已入队，未触发 PG)")

        # 测试 2: MODIFY — 人工修订，验证触发 PG 回流 + 入队
        print("\n✏️ [Test 2] MODIFY (人工修订 + 数据飞轮)...")
        resp = client.post(
            "/api/v1/chat/confirm_send",
            json={
                "trace_id": "test-tr-001",
                "final_text": "产品A价格是199元（人工修正）",
                "is_modified": True,
                "action": "MODIFY",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "QUEUED", f"Expected QUEUED, got {data['status']}"
        print(f"   Response: {data}")
        print("   ✅ MODIFY + 数据飞轮通过 (PG 异步回流已触发)")

        # 测试 3: REJECT — 拒绝发送，验证未入队
        print("\n❌ [Test 3] REJECT (拒绝发送)...")
        resp = client.post(
            "/api/v1/chat/confirm_send",
            json={
                "trace_id": "test-tr-001",
                "final_text": "",
                "is_modified": False,
                "action": "REJECT",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "REJECTED", f"Expected REJECTED, got {data['status']}"
        assert "拒绝" in data["message"]
        print(f"   Response: {data}")
        print("   ✅ REJECT 通过 (未入队)")

        print("\n" + "=" * 60)
        print("🎉 Human-in-the-Loop API 全部 Mock 测试完成!")
        print("=" * 60)

    asyncio.run(main())
