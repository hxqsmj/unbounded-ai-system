"""
无界AI超级员工系统 - FastAPI 应用入口 (V3.0)

启动方式:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

生命周期:
  startup:  初始化 AIBrain + HumanLoop + TypingSimulator + Redis Worker
  shutdown: 优雅关闭所有连接池与后台任务
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.services.ai_brain import (
    AIBrain,
    ai_brain_router,
    set_brain,
    set_on_generated,
)
from app.services.human_loop import (
    HumanLoop,
    human_loop_router,
    set_loop,
)
from app.services.typing_simulator import TypingSimulator

# ════════════════════════════════════════════════════════════
# 全局服务引用
# ════════════════════════════════════════════════════════════

_brain: Optional[AIBrain] = None
_loop: Optional[HumanLoop] = None
_simulator: Optional[TypingSimulator] = None
_worker_task: Optional[asyncio.Task] = None


# ════════════════════════════════════════════════════════════
# WebSocket 连接管理 (推送新消息到前端审核面板)
# ════════════════════════════════════════════════════════════

class WSManager:
    """管理前端审核面板的 WebSocket 连接池"""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, data: dict[str, Any]) -> None:
        """向所有已连接的前端客户端广播消息"""
        payload = json.dumps(data, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


_ws_manager = WSManager()


def notify_frontend(trace_data: dict[str, Any]) -> None:
    """非阻塞通知前端有新消息待审核"""
    asyncio.create_task(_ws_manager.broadcast({
        "type": "new_message",
        "data": trace_data,
    }))


# ════════════════════════════════════════════════════════════
# 应用生命周期
# ════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理。

    Startup:
      1. 初始化 TypingSimulator (含 Redis 连接)
      2. 初始化 AIBrain (Qdrant + MongoDB)
      3. 初始化 HumanLoop (MongoDB + PostgreSQL + TypingSimulator)
      4. 启动 Redis 消费 Worker (如已配置)

    Shutdown:
      1. 停止 Worker
      2. 关闭所有连接
    """
    global _brain, _loop, _simulator, _worker_task

    # ── Startup ─────────────────────────────────────────────
    print("=" * 60)
    print(f"🚀 {settings.app_name} v{settings.app_version} starting...")
    print("=" * 60)

    # 1. TypingSimulator
    _simulator = TypingSimulator(redis_url=settings.redis_url)
    print(f"   TypingSimulator: {'Redis' if settings.redis_url else 'Memory'} mode")

    # 2. AIBrain
    _brain = AIBrain()
    set_brain(_brain)
    set_on_generated(lambda data: notify_frontend(data))
    print("   AIBrain: initialized (+ WS push)")

    # 3. HumanLoop
    _loop = HumanLoop(typing_simulator=_simulator)
    set_loop(_loop)
    print("   HumanLoop: initialized")

    # 4. Redis Worker (如已配置 Redis)
    # 生产环境: send_callback 应指向 WSS Gateway 的 send_to_hook 方法
    # 当 WSS Gateway 未启动时，降级为日志打印
    if settings.redis_url:
        async def _default_send(account_id: str, to_user: str, text: str):
            """默认发送回调 — 生产环境由 WSS Gateway 注入 send_to_hook"""
            print(f"[Worker::Send] {account_id} → {to_user}: '{text[:60]}...'")

        _worker_task = _simulator.start_worker(
            send_callback=_default_send,
            poll_interval=1.0,
        )
        print(f"   Redis Worker: started (task: {id(_worker_task)})")

    print("=" * 60)
    print(f"✅ {settings.app_name} ready.")
    print("=" * 60)

    yield  # ── 应用运行中 ──

    # ── Shutdown ────────────────────────────────────────────
    print("=" * 60)
    print("🛑 Shutting down...")
    print("=" * 60)

    if _simulator:
        await _simulator.stop_worker()
        print("   TypingSimulator: stopped")

    if _brain:
        await _brain.close()
        print("   AIBrain: closed")

    if _loop:
        await _loop.close()
        print("   HumanLoop: closed")

    print("=" * 60)
    print("👋 Goodbye.")
    print("=" * 60)


# ════════════════════════════════════════════════════════════
# FastAPI 应用实例
# ════════════════════════════════════════════════════════════

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="无界AI超级员工系统 - 全异步、高防封、人机协作智能客服平台",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境需收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(ai_brain_router)
app.include_router(human_loop_router)


# ════════════════════════════════════════════════════════════
# WebSocket 推送端点 (前端审核面板实时接收新消息)
# ════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """前端审核面板 WebSocket 连接"""
    await _ws_manager.connect(ws)
    try:
        while True:
            # 接收前端心跳 ping，回复 pong
            data = await ws.receive_text()
            msg = json.loads(data) if data else {}
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_manager.disconnect(ws)


# ════════════════════════════════════════════════════════════
# 健康检查
# ════════════════════════════════════════════════════════════

@app.get("/health", tags=["System"])
async def health_check():
    """健康检查端点"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "redis_mode": "enabled" if settings.redis_url else "memory",
    }


@app.get("/", tags=["System"])
async def root():
    """根路径"""
    return {
        "message": f"欢迎使用{settings.app_name}",
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


# ════════════════════════════════════════════════════════════
# 直接运行
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
