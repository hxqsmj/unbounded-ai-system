"""
Task 2: AI 智脑与 RAG 检索链路 (AI Brain V3.0)

功能:
  1. Qdrant 向量检索 Top-N 文档
  2. 阈值拒答 (max_score < qdrant_score_threshold → 兜底回复)
  3. System Prompt 组装 + LLM 调用
  4. 异步 MongoDB trace_log 日志
  5. FastAPI API 端点 POST /api/v1/chat/generate

依赖:
  - fastapi>=0.100.0
  - qdrant-client>=1.4.0
  - motor>=3.2.0
  - httpx>=0.24.0
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException

# 可选依赖 — lazy import 以支持 Mock 测试
try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as qdrant_models
except ImportError:
    AsyncQdrantClient = None  # type: ignore
    qdrant_models = None  # type: ignore

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None  # type: ignore

from app.core.config import settings
from app.core.schemas import (
    ChatGenerateRequest,
    ChatGenerateResponse,
    ChatMessage,
    TraceLogModel,
)


# ════════════════════════════════════════════════════════════
# AI Brain 核心类
# ════════════════════════════════════════════════════════════

class AIBrain:
    """
    AI 智脑：RAG 检索 → LLM 推理 → 日志记录 全链路处理器。

    架构:
      Qdrant (向量检索) → 阈值判断 → LLM (httpx) → MongoDB (motor)
    """

    def __init__(
        self,
        qdrant_client: Optional["AsyncQdrantClient"] = None,
        mongo_client: Optional["AsyncIOMotorClient"] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        """
        Args:
            qdrant_client: Qdrant 异步向量数据库客户端
            mongo_client:  MongoDB 异步客户端
            http_client:   httpx 异步 HTTP 客户端 (用于 LLM 调用)
        """
        self.qdrant = qdrant_client
        self.mongo = mongo_client
        self.http = http_client or httpx.AsyncClient(timeout=30.0)

        # MongoDB 集合引用 (惰性初始化)
        self._trace_collection = None

    # ── 依赖注入 ────────────────────────────────────────────

    async def _ensure_mongo(self) -> None:
        """惰性初始化 MongoDB 连接"""
        if self._trace_collection is None:
            if self.mongo is None:
                self.mongo = AsyncIOMotorClient(settings.mongo_uri)
            db = self.mongo[settings.mongo_db]
            self._trace_collection = db[settings.mongo_trace_collection]

    async def _ensure_qdrant(self) -> None:
        """惰性初始化 Qdrant 异步连接"""
        if self.qdrant is None:
            self.qdrant = AsyncQdrantClient(
                host=settings.qdrant_host,
                port=settings.qdrant_port,
            )

    # ── Embedding 查询向量化 ──────────────────────────────────

    async def _embed_query(self, text: str) -> List[float]:
        """
        调用 Embedding API 将查询文本转为向量。

        使用独立的 embedding_api_base/key/model，
        与 LLM 对话模型完全解耦。
        """
        url = f"{settings.embedding_api_base.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {settings.embedding_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.embedding_model,
            "input": [text],
            "encoding_format": "float",
        }

        try:
            response = await self.http.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            items = data.get("data", [])
            if items:
                return items[0].get("embedding", [])
        except Exception as e:
            print(f"[AIBrain] ⚠️ Embedding query failed: {e}")

        # 降级：返回空列表（将触发拒答）
        return []

    # ── RAG 检索 ────────────────────────────────────────────

    async def retrieve_documents(
        self, query: str, top_k: Optional[int] = None
    ) -> List[dict]:
        """
        Qdrant 向量检索。

        流程:
          1. 调用 Embedding API 将查询文本向量化
          2. 使用真实向量在 Qdrant 中检索 Top-N
          3. Embedding 失败时降级返回空列表

        Args:
            query:  用户查询文本
            top_k:  返回文档数 (默认取配置值)

        Returns:
            [{"text": "...", "score": 0.85, "metadata": {...}}, ...]
        """
        await self._ensure_qdrant()
        top_k = top_k or settings.qdrant_top_k

        # Step 1: 查询文本 → 向量
        query_vector = await self._embed_query(query)
        if not query_vector:
            print(f"[AIBrain] ⚠️ Query embedding failed, returning empty results.")
            return []

        # Step 2: Qdrant 向量检索
        try:
            response = await self.qdrant.query_points(
                collection_name=settings.qdrant_collection,
                query=query_vector,
                limit=top_k,
                with_payload=True,
            )
            results = response.points
        except Exception:
            # Qdrant 不可用时返回空列表（降级）
            print(f"[AIBrain] ⚠️ Qdrant unavailable, returning empty results.")
            return []

        documents = []
        for hit in results:
            documents.append({
                "text": hit.payload.get("text", "") if hit.payload else "",
                "score": hit.score,
                "metadata": hit.payload.get("metadata", {}) if hit.payload else {},
            })

        return documents

    # ── LLM 调用 ─────────────────────────────────────────────

    async def call_llm(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[List[ChatMessage]] = None,
    ) -> str:
        """
        调用 LLM 生成回复。

        Args:
            system_prompt: 系统提示词
            user_message:  用户最新消息
            history:       历史对话

        Returns:
            LLM 生成的回复文本
        """
        messages = [{"role": "system", "content": system_prompt}]

        # 注入历史对话
        if history:
            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self.http.post(
                f"{settings.llm_api_base}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            print(f"[AIBrain] ⚠️ LLM call failed: {e}")
            # 降级：返回兜底回复
            return settings.fallback_reply

    # ── 异步日志 ────────────────────────────────────────────

    async def log_trace(self, log: TraceLogModel) -> None:
        """
        异步写入 MongoDB trace_logs。

        Args:
            log: TraceLogModel 实例
        """
        await self._ensure_mongo()
        try:
            await self._trace_collection.insert_one(
                log.model_dump()
            )
            print(f"[AIBrain] Trace logged: {log.trace_id}")
        except Exception as e:
            print(f"[AIBrain] ⚠️ Failed to log trace {log.trace_id}: {e}")

    # ── 核心处理流程 ────────────────────────────────────────

    async def generate(
        self,
        account_id: str,
        customer_id: str,
        user_message: str,
        history: Optional[List[ChatMessage]] = None,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> ChatGenerateResponse:
        """
        AI 生成主流程: 检索 → 阈值判断 → LLM → 日志。

        处理流程:
          1. 生成唯一 UUID trace_id
          2. Qdrant 异步检索 Top-3 文档，提取 max_score
          3. 断路防幻觉: max_score < 0.65 → 直接返回兜底拒答词，绝不调用 LLM
          4. 组装 System Prompt + Context，异步调用 LLM
          5. 非阻塞写入 MongoDB trace_log

        Args:
            account_id:       操作账号ID
            customer_id:      客户ID
            user_message:     用户消息
            history:          历史对话
            background_tasks: FastAPI BackgroundTasks (用于非阻塞日志落盘)

        Returns:
            ChatGenerateResponse (trace_id + generated_text + max_score + status)
        """
        trace_id = f"tr-{uuid.uuid4().hex[:8]}"  # 格式: tr-xxxxxxxx
        history = history or []

        # Step 1: RAG 检索 — 使用 AsyncQdrantClient 异步检索 Top-N
        retrieved_docs = await self.retrieve_documents(user_message)

        # Step 2: 断路防幻觉拦截 — 提取最高得分，低于阈值直接拒答
        is_fallback = False
        generated_text: str
        max_score: Optional[float] = None

        if retrieved_docs:
            max_score = max(doc["score"] for doc in retrieved_docs)
            if max_score < settings.qdrant_score_threshold:
                # 强制返回兜底拒答词，绝不继续调用 LLM
                is_fallback = True
                generated_text = settings.fallback_reply
                print(
                    f"[AIBrain] Trace: {trace_id} | "
                    f"Max score {max_score:.3f} < threshold {settings.qdrant_score_threshold} → Fallback (断路拒答)"
                )
        else:
            # 无检索结果也触发拒答（Qdrant 超时/空库兜底）
            is_fallback = True
            generated_text = settings.fallback_reply
            print(f"[AIBrain] Trace: {trace_id} | No documents retrieved → Fallback")

        # Step 3: 仅在不拒答时调用 LLM (避免幻觉 + 节省 Token)
        if not is_fallback:
            context = "\n\n".join(
                f"[来源 {i+1} | 相关度: {doc['score']:.2f}] {doc['text']}"
                for i, doc in enumerate(retrieved_docs)
            )
            system_prompt = settings.system_prompt_template.format(context=context)

            generated_text = await self.call_llm(
                system_prompt=system_prompt,
                user_message=user_message,
                history=history,
            )

        # Step 4: 非阻塞 Trace 日志落盘
        # 优先使用 FastAPI BackgroundTasks，直接调用时降级为 asyncio.create_task
        trace_log = TraceLogModel(
            trace_id=trace_id,
            timestamp=datetime.now(timezone.utc),
            account_id=account_id,
            customer_id=customer_id,
            user_input=user_message,
            retrieved_docs=retrieved_docs,
            prompt_system=settings.system_prompt_template.format(
                context="\n\n".join(d["text"] for d in retrieved_docs)
            ) if not is_fallback else None,
            llm_raw_output=generated_text if not is_fallback else None,
            status="PENDING",
        )

        if background_tasks is not None:
            # FastAPI 上下文 — BackgroundTasks 确保响应返回后任务仍执行
            background_tasks.add_task(self.log_trace, trace_log)
        else:
            # 直接调用 / Mock 测试 上下文 — 降级为 create_task
            import asyncio
            asyncio.create_task(self.log_trace(trace_log))

        return ChatGenerateResponse(
            trace_id=trace_id,
            generated_text=generated_text,
            is_fallback=is_fallback,
            max_score=max_score,
            status="PENDING",
        )

    async def close(self) -> None:
        """优雅关闭连接"""
        await self.http.aclose()
        if self.qdrant:
            await self.qdrant.close()
        print("[AIBrain] Connections closed.")


# ════════════════════════════════════════════════════════════
# FastAPI 路由
# ════════════════════════════════════════════════════════════

ai_brain_router = APIRouter(prefix="/api/v1/chat", tags=["AI Brain"])

# 全局 AIBrain 实例 (由 main.py 在 startup 时注入)
_brain_instance: Optional[AIBrain] = None
_on_generated_callback: Optional[callable] = None  # 通知前端的回调


def get_brain() -> AIBrain:
    """依赖注入: 获取 AIBrain 单例"""
    if _brain_instance is None:
        raise HTTPException(status_code=500, detail="AI Brain not initialized.")
    return _brain_instance


def set_brain(brain: AIBrain) -> None:
    """设置全局 AIBrain 实例"""
    global _brain_instance
    _brain_instance = brain


def set_on_generated(callback: callable) -> None:
    """设置生成完成后的通知回调 (main.py 注入 WS 广播)"""
    global _on_generated_callback
    _on_generated_callback = callback


@ai_brain_router.post("/generate", response_model=ChatGenerateResponse)
async def chat_generate(request: ChatGenerateRequest, background_tasks: BackgroundTasks):
    """
    AI 智脑生成接口。

    处理流程:
      1. 接收用户消息和历史对话
      2. Qdrant AsyncQdrantClient 异步检索 Top-3 文档
      3. 断路防幻觉: max_score < 0.65 → 直接返回兜底拒答词，绝不调用 LLM
      4. 组装 System Prompt + Context，异步调用 LLM 生成回复
      5. 非阻塞写入 MongoDB trace_log (通过 BackgroundTasks)
      6. 推送新消息到前端审核面板 WebSocket
    """
    brain = get_brain()
    result = await brain.generate(
        account_id=request.account_id,
        customer_id=request.customer_id,
        user_message=request.user_message,
        history=request.history,
        background_tasks=background_tasks,
    )
    # 非阻塞通知前端审核面板
    if _on_generated_callback:
        try:
            import asyncio
            asyncio.create_task(_on_generated_callback({
                "trace_id": result.trace_id,
                "account_id": request.account_id,
                "customer_id": request.customer_id,
                "user_input": request.user_message,
                "generated_text": result.generated_text,
                "max_score": result.max_score,
                "is_fallback": result.is_fallback,
                "status": result.status,
            }))
        except Exception:
            pass
    return result


# ════════════════════════════════════════════════════════════
# Mock 测试块 (if __name__ == '__main__')
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    使用 FastAPI TestClient 一键验证 AI Brain API:
      1. 正常生成流程
      2. 兜底拒答逻辑 (低分文档场景)
      3. 空检索结果拒答
    """
    import asyncio
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    # ── 构建测试用 FastAPI 应用 ─────────────────────────────
    test_app = FastAPI()
    test_app.include_router(ai_brain_router)

    # ── Mock AIBrain (不依赖真实 Qdrant / LLM / MongoDB) ───
    class MockAIBrain(AIBrain):
        """Mock AIBrain: 模拟检索与 LLM 调用，不依赖外部服务"""

        async def retrieve_documents(self, query: str, top_k=None):
            # 模拟检索: 根据查询内容返回不同置信度
            if "已知" in query or "产品" in query:
                return [
                    {"text": "这是产品A的详细说明文档", "score": 0.88, "metadata": {}},
                    {"text": "产品A常见问题解答", "score": 0.76, "metadata": {}},
                    {"text": "产品A使用手册", "score": 0.70, "metadata": {}},
                ]
            elif "模糊" in query:
                return [
                    {"text": "不太相关的文档片段", "score": 0.45, "metadata": {}},
                ]
            else:
                return []

        async def call_llm(self, system_prompt, user_message, history=None):
            return f"这是对「{user_message}」的 AI 生成回复（Mock）"

        async def log_trace(self, log):
            print(f"   [Mock] Trace logged: {log.trace_id}")

    # ── 注入 Mock Brain ─────────────────────────────────────
    mock_brain = MockAIBrain()
    set_brain(mock_brain)

    client = TestClient(test_app)

    # ── 运行测试 ────────────────────────────────────────────
    async def main():
        print("=" * 60)
        print("🧪 AI Brain API Mock 测试")
        print("=" * 60)

        # 测试 1: 正常生成 (高置信度检索)
        print("\n📝 [Test 1] 正常生成流程 (高置信度)...")
        resp = client.post(
            "/api/v1/chat/generate",
            json={
                "account_id": "sales_01",
                "customer_id": "cust_001",
                "user_message": "已知产品A的价格是多少？",
                "history": [],
            },
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}"
        data = resp.json()
        assert "trace_id" in data, "Missing trace_id"
        assert not data["is_fallback"], "Should not be fallback"
        assert data["status"] == "PENDING"
        print(f"   Trace: {data['trace_id']}")
        print(f"   Generated: '{data['generated_text'][:60]}...'")
        print("   ✅ 正常生成通过")

        # 测试 2: 兜底拒答 (低置信度)
        print("\n🛡️ [Test 2] 低置信度拒答...")
        resp = client.post(
            "/api/v1/chat/generate",
            json={
                "account_id": "sales_01",
                "customer_id": "cust_002",
                "user_message": "模糊的问题",
                "history": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_fallback"], "Should be fallback (low score)"
        assert data["generated_text"] == settings.fallback_reply
        print(f"   Fallback: '{data['generated_text']}'")
        print("   ✅ 低分拒答通过")

        # 测试 3: 空检索结果拒答
        print("\n🛡️ [Test 3] 空检索结果拒答...")
        resp = client.post(
            "/api/v1/chat/generate",
            json={
                "account_id": "sales_01",
                "customer_id": "cust_003",
                "user_message": "随便聊聊",
                "history": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_fallback"], "Should be fallback (no docs)"
        print(f"   Fallback: '{data['generated_text']}'")
        print("   ✅ 空结果拒答通过")

        # 测试 4: 带历史对话的请求
        print("\n💬 [Test 4] 带历史对话的生成...")
        resp = client.post(
            "/api/v1/chat/generate",
            json={
                "account_id": "sales_01",
                "customer_id": "cust_004",
                "user_message": "已知产品A的保修政策？",
                "history": [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "您好！有什么可以帮您的？"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert not data["is_fallback"]
        print(f"   Trace: {data['trace_id']}")
        print("   ✅ 历史对话请求通过")

        print("\n" + "=" * 60)
        print("🎉 AI Brain API 全部 Mock 测试完成!")
        print("=" * 60)

    asyncio.run(main())
