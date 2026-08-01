"""
无界AI超级员工系统 - FastAPI 应用入口 (V4.0 生产级)

启动方式:
  开发: uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
  生产: uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4

生命周期:
  startup:  初始化 AIBrain + HumanLoop + TypingSimulator + Redis Worker
  shutdown: 优雅关闭所有连接池与后台任务
"""

import asyncio
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None  # type: ignore

from app.core.config import settings
from app.core.security import auth_enabled, verify_ws_token
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
from app.services.wss_gateway import WSSGateway

# ════════════════════════════════════════════════════════════
# 全局服务引用
# ════════════════════════════════════════════════════════════

_brain: Optional[AIBrain] = None
_loop: Optional[HumanLoop] = None
_simulator: Optional[TypingSimulator] = None
_worker_task: Optional[asyncio.Task] = None
_gateway: Optional[WSSGateway] = None


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
    global _brain, _loop, _simulator, _worker_task, _gateway

    # ── Startup ─────────────────────────────────────────────
    print("=" * 60)
    print(f"🚀 {settings.app_name} v{settings.app_version} starting...")
    print("=" * 60)

    # ⚠ 鉴权状态检查
    if not auth_enabled():
        print("=" * 60)
        print("⚠️⚠️ 警告: 未设置 API_TOKEN，系统当前无鉴权状态！")
        print("   任何能访问本服务的人都可以读取/操作全部客户对话。")
        print("   生产环境必须在 .env 中设置 API_TOKEN！")
        print("=" * 60)

    # 1. TypingSimulator
    _simulator = TypingSimulator(redis_url=settings.redis_url)
    print(f"   TypingSimulator: {'Redis' if settings.redis_url else 'Memory'} mode")

    # 2. AIBrain
    _brain = AIBrain()
    set_brain(_brain)
    # 修复: 注入 async 包装，避免 ai_brain 中 create_task(同步返回值) 抛 TypeError
    async def _on_generated_async(data: dict[str, Any]) -> None:
        notify_frontend(data)
    set_on_generated(_on_generated_async)
    print("   AIBrain: initialized (+ WS push)")

    # 3. HumanLoop
    _loop = HumanLoop(typing_simulator=_simulator)
    set_loop(_loop)
    print("   HumanLoop: initialized")

    # 4. WSS Gateway (生产必须开启) — 启动后其 send_to_hook 就是发送回调
    if settings.wss_enabled:
        _gateway = WSSGateway(
            host=settings.wss_host,
            port=settings.wss_port,
        )
        await _gateway.start()
        print(f"   WSS Gateway: started on ws://{settings.wss_host}:{settings.wss_port}")
    else:
        _gateway = None
        print("   WSS Gateway: ⚠️ 已禁用 (wss_enabled=false)，微信发送链路不可用！")

    # 5. Redis Worker (如已配置 Redis)
    # 修复: send_callback 指向 WSS Gateway 的 send_to_hook —— 审核采纳后消息真正发到微信 Hook
    if settings.redis_url:
        if _gateway is not None:
            send_callback = _gateway.get_send_callback()
        else:
            async def send_callback(account_id: str, to_user: str, text: str):
                print(f"[Worker::Send] ⚠️ WSS Gateway 未启动，仅日志: {account_id} → {to_user}: '{text[:60]}...'")
                raise RuntimeError("WSS Gateway 未启动，消息未能发送")

        _worker_task = _simulator.start_worker(
            send_callback=send_callback,
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

    if _gateway is not None:
        await _gateway.stop()
        print("   WSS Gateway: stopped")

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

# ── 简易速率限制中间件 (生产级，无需额外依赖) ──────────────
# 全局: 每 IP 每分钟最多 120 请求
# API 写入端点: 每 IP 每分钟最多 30 请求

_rate_window = 60  # 窗口秒数
_rate_global_max = 120  # 全局最大
_rate_write_max = 30    # 写入端点最大

_rate_store: dict[str, list[float]] = defaultdict(list)
_rate_store_lock = asyncio.Lock()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    简易滑动窗口速率限制。

    修复: 应用位于 Nginx 反代之后时 request.client.host 恒为 127.0.0.1，
    改用 X-Forwarded-For 首地址取真实客户端 IP（Nginx 已配置 proxy_set_header）。
    注意: --workers > 1 时每进程独立计数，精确限速仍需依赖 Nginx limit_req。
    """
    # 获取客户端 IP: 优先取 X-Forwarded-For 第一个地址（最外层真实 IP）
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        client_ip = xff.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    # 写入端点更严格
    is_write = request.method in ("POST", "PUT", "PATCH", "DELETE")
    max_req = _rate_write_max if is_write else _rate_global_max

    now = time.time()
    cutoff = now - _rate_window

    async with _rate_store_lock:
        # 清理过期记录
        _rate_store[client_ip] = [t for t in _rate_store[client_ip] if t > cutoff]

        if len(_rate_store[client_ip]) >= max_req:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试", "retry_after": _rate_window},
            )

        _rate_store[client_ip].append(now)

    response = await call_next(request)
    return response


# CORS 中间件 (生产环境: 允许 localhost + 服务器自身)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # 本地开发
        "http://localhost:8001",   # 本地后端
        "http://127.0.0.1:8001",
        "http://127.0.0.1",       # Nginx 前端
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(ai_brain_router)
app.include_router(human_loop_router)


# ════════════════════════════════════════════════════════════
# WebSocket 推送端点 (前端审核面板实时接收新消息)
# ════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query("")):
    """
    前端审核面板 WebSocket 连接。

    鉴权: settings.api_token 非空时必须携带 ?token=xxx，否则拒绝连接。
    """
    if not verify_ws_token(token):
        print(f"[WS] 🔒 拒绝未授权连接 (token 缺失/无效)")
        await ws.close(code=1008, reason="unauthorized: invalid token")
        return

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

async def _check_dependency_health() -> dict:
    """检查各外部依赖连通性（每个 2 秒超时，失败不抛异常）"""
    deps: dict[str, bool] = {}

    # Redis
    try:
        r = _simulator.redis or (await _simulator.init_redis() or _simulator.redis)
        await asyncio.wait_for(r.ping(), timeout=2.0)
        deps["redis"] = True
    except Exception:
        deps["redis"] = False

    # Qdrant
    try:
        q = _brain.qdrant or (await _brain._ensure_qdrant() or _brain.qdrant)
        await asyncio.wait_for(q.get_collections(), timeout=2.0)
        deps["qdrant"] = True
    except Exception:
        deps["qdrant"] = False

    # MongoDB
    try:
        m = _brain.mongo or AsyncIOMotorClient(settings.mongo_uri)
        await asyncio.wait_for(m.admin.command("ping"), timeout=2.0)
        deps["mongo"] = True
    except Exception:
        deps["mongo"] = False

    # PostgreSQL
    try:
        import asyncpg
        pg = await asyncio.wait_for(
            asyncpg.connect(dsn=settings.pg_dsn, timeout=2), timeout=3.0
        )
        await pg.fetchval("SELECT 1")
        await pg.close()
        deps["postgres"] = True
    except Exception:
        deps["postgres"] = False

    return deps


@app.get("/health", tags=["System"])
async def health_check():
    """
    健康检查端点 — 修复: 实际探测各依赖连通性。

    HTTP 200 保持（部署脚本按 200 判断），status 字段区分:
      ok      — 全部依赖正常
      degraded — 部分依赖不可用（deps 中列出明细）
    """
    deps = await _check_dependency_health()
    all_ok = all(deps.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "redis_mode": "enabled" if settings.redis_url else "memory",
        "auth": "enabled" if auth_enabled() else "disabled",
        "deps": deps,
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
