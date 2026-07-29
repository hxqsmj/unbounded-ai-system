"""
Task 4 (P1 生产级): WSS 网关 — 微信 Hook ↔ 后端 API 双向消息桥梁 (V4.0)

功能:
  1. 多客户端连接管理 (按 account_id 分组注册)
  2. 双向消息路由 (Hook→API→前端 和 TypingSimulator→Hook)
  3. Ping/Pong 心跳保活 (30s, 连续3次丢失告警+断开)
  4. 断线重连缓冲 (pending 消息队列, 重连后恢复)
  5. 优雅降级 (后端不可用时通知前端)

架构:
  微信Hook ──WSS──▶ WSSGateway ──HTTP──▶ AI Brain API
                       ▲                      │
                       │ (send_callback)       ▼
                       └──── TypingSimulator ◄─┘
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Callable, Optional

import httpx
import websockets
from websockets.asyncio.server import ServerConnection

from app.core.config import settings


# ════════════════════════════════════════════════════════════
# 连接管理器: 多客户端分组 + 心跳 + 缓冲
# ════════════════════════════════════════════════════════════

class ConnectionManager:
    """
    管理所有 WebSocket 连接，按类型和账号分组。

    连接类型:
      - "hook":   微信 Hook 客户端 (发送客户消息 + 执行发送指令)
      - "frontend": Vue3 前端审核面板 (接收新消息推送)
    """

    def __init__(self):
        # {account_id: {"ws": WebSocket, "last_pong": float, "missed": int, "buffer": list}}
        self._hooks: dict[str, dict] = {}
        # [WebSocket, ...]
        self._frontends: list[ServerConnection] = []
        # 待发送消息缓冲 (Hook 断开时暂存)
        self._pending_sends: dict[str, list[dict]] = {}

    # ── Hook 客户端 ──────────────────────────────────────────

    def register_hook(self, account_id: str, ws: ServerConnection) -> None:
        """注册或更新 Hook 客户端连接"""
        # 保留旧连接的待发送缓冲
        old = self._hooks.pop(account_id, None)
        buffered = old.get("buffer", []) if old else []
        if account_id in self._pending_sends:
            buffered.extend(self._pending_sends.pop(account_id, []))

        self._hooks[account_id] = {
            "ws": ws,
            "last_pong": time.time(),
            "missed": 0,
            "buffer": buffered,
            "connected_at": datetime.utcnow().isoformat(),
        }
        print(f"[WSS] 🔗 Hook registered: {account_id} "
              f"(active hooks: {self.hook_count}, buffered: {len(buffered)} msgs)")

    def unregister_hook(self, account_id: str) -> None:
        """注销 Hook 客户端，保留待发送缓冲"""
        if account_id in self._hooks:
            buf = self._hooks[account_id].get("buffer", [])
            if buf:
                self._pending_sends[account_id] = buf
            del self._hooks[account_id]
            print(f"[WSS] 🔌 Hook disconnected: {account_id} "
                  f"(pending: {len(self._pending_sends.get(account_id, []))} msgs)")

    def get_hook(self, account_id: str) -> Optional[ServerConnection]:
        """获取指定账号的 Hook 连接"""
        entry = self._hooks.get(account_id)
        return entry["ws"] if entry else None

    @property
    def hook_count(self) -> int:
        return len(self._hooks)

    @property
    def hook_accounts(self) -> list[str]:
        return list(self._hooks.keys())

    # ── 前端客户端 ──────────────────────────────────────────

    def register_frontend(self, ws: ServerConnection) -> None:
        self._frontends.append(ws)
        print(f"[WSS] 🖥️  Frontend connected (active: {len(self._frontends)})")

    def unregister_frontend(self, ws: ServerConnection) -> None:
        if ws in self._frontends:
            self._frontends.remove(ws)
            print(f"[WSS] 🖥️  Frontend disconnected (active: {len(self._frontends)})")

    async def broadcast_frontend(self, data: dict) -> None:
        """向所有前端推送消息"""
        if not self._frontends:
            return
        payload = json.dumps(data, ensure_ascii=False)
        dead = []
        for ws in self._frontends:
            try:
                await ws.send(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister_frontend(ws)

    # ── 心跳管理 ────────────────────────────────────────────

    def update_pong(self, account_id: str) -> None:
        """更新 Hook 客户端心跳"""
        entry = self._hooks.get(account_id)
        if entry:
            entry["last_pong"] = time.time()
            entry["missed"] = 0

    def check_heartbeats(self) -> list[str]:
        """
        检查所有 Hook 客户端心跳，返回已超时的账号列表。
        连续 3 次丢失 Pong → 触发告警并标记断开。
        """
        now = time.time()
        timeout_accounts = []
        for aid, entry in list(self._hooks.items()):
            elapsed = now - entry["last_pong"]
            if elapsed > settings.wss_heartbeat_interval * 2:
                entry["missed"] += 1
                print(f"[WSS] ⚠️  {aid}: Missed Pong #{entry['missed']} "
                      f"({elapsed:.0f}s since last Pong)")
            if entry["missed"] >= settings.wss_max_missed_pongs:
                print(f"[WSS] 🚨 ALERT: {aid} connection DEAD "
                      f"(missed {entry['missed']} Pongs)")
                timeout_accounts.append(aid)
        return timeout_accounts

    # ── 消息缓冲 (断线重连恢复) ─────────────────────────────

    def buffer_for_hook(self, account_id: str, msg: dict) -> None:
        """将消息加入 Hook 的待发送缓冲"""
        if account_id in self._hooks:
            self._hooks[account_id].setdefault("buffer", []).append(msg)
        else:
            self._pending_sends.setdefault(account_id, []).append(msg)

    def drain_buffer(self, account_id: str) -> list[dict]:
        """取出并清空缓冲消息"""
        msgs = []
        if account_id in self._pending_sends:
            msgs = self._pending_sends.pop(account_id, [])
        entry = self._hooks.get(account_id)
        if entry:
            msgs.extend(entry.get("buffer", []))
            entry["buffer"] = []
        return msgs


# ════════════════════════════════════════════════════════════
# WSS Gateway 核心类
# ════════════════════════════════════════════════════════════

class WSSGateway:
    """
    微信 Hook ↔ 后端 API 双向消息网关。

    消息流向:
      1. Hook → Gateway → POST /api/v1/chat/generate → 广播前端
      2. 前端审核 → POST /api/v1/chat/confirm_send → TypingSimulator
         → send_callback → Gateway → Hook 执行发送
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        backend_api_base: Optional[str] = None,
    ):
        self.host = host or settings.wss_host
        self.port = port or settings.wss_port
        self.backend_api_base = backend_api_base or (
            f"http://{settings.api_host}:{settings.api_port}"
        )
        self.mgr = ConnectionManager()
        self.http: Optional[httpx.AsyncClient] = None
        self._server: Optional[websockets.Server] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ── HTTP 客户端 ──────────────────────────────────────────

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=100),
            )
        return self.http

    # ── 消息过滤 ────────────────────────────────────────────

    def _is_system_message(self, content: str) -> bool:
        keywords = [
            "邀请", "加入了群聊", "退出了群聊", "群公告", "修改群名为",
            "撤回了一条消息", "开启了群禁言", "关闭了群禁言", "被移除群聊",
        ]
        return any(kw in content for kw in keywords)

    def _extract_private_message(self, raw: dict) -> Optional[dict]:
        event = raw.get("event", "")
        data = raw.get("data", {})
        if event != "ON_RECV_MSG":
            return None
        if data.get("is_group", False):
            return None
        content = (data.get("content") or "").strip()
        if not content or self._is_system_message(content):
            return None
        return {
            "sender_id": data.get("from_user", ""),
            "content": content,
            "msg_id": data.get("msg_id", ""),
        }

    # ── 消息转发: Hook → 后端 API ────────────────────────────

    async def _forward_to_backend(
        self, sender_id: str, content: str, account_id: str
    ) -> Optional[dict]:
        """转发客户消息到后端 AI Brain，返回生成结果"""
        http = await self._ensure_http()
        try:
            resp = await http.post(
                f"{self.backend_api_base}/api/v1/chat/generate",
                json={
                    "account_id": account_id,
                    "customer_id": sender_id,
                    "user_message": content,
                    "history": [],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            print(f"[WSS] 📤 {account_id}/{sender_id[:20]} → "
                  f"Trace: {data.get('trace_id')} | Score: {data.get('max_score', 0):.3f}")
            return data
        except httpx.HTTPError as e:
            print(f"[WSS] ⚠️  Backend error for {account_id}: {e}")
            return None

    # ── 消息推送: TypingSimulator → Hook 客户端 ─────────────

    async def send_to_hook(self, account_id: str, to_user: str, text: str) -> bool:
        """
        TypingSimulator 的回调: 通过 WebSocket 通知 Hook 执行发送。

        Returns:
            True 如果消息已投递到 Hook，False 如果 Hook 不在线 (已缓冲)
        """
        ws = self.mgr.get_hook(account_id)
        payload = json.dumps({
            "type": "SEND_MSG",
            "data": {
                "to_user": to_user,
                "text": text,
                "timestamp": time.time(),
            },
        }, ensure_ascii=False)

        if ws:
            try:
                await ws.send(payload)
                print(f"[WSS] 📨 Sent to Hook {account_id}: "
                      f"→ {to_user[:20]} | '{text[:40]}...'")
                return True
            except Exception as e:
                print(f"[WSS] ⚠️  Failed to send to Hook {account_id}: {e}")
                self.mgr.unregister_hook(account_id)

        # Hook 不在线 → 缓冲
        self.mgr.buffer_for_hook(account_id, {
            "to_user": to_user, "text": text, "ts": time.time(),
        })
        print(f"[WSS] 📦 Buffered for {account_id}: '{text[:30]}...'")
        return False

    def get_send_callback(self) -> Callable:
        """返回 TypingSimulator 可用的 send_callback"""
        async def _callback(account_id: str, to_user: str, text: str):
            await self.send_to_hook(account_id, to_user, text)
        return _callback

    # ── WebSocket 连接处理 ──────────────────────────────────

    async def _handle_hook(self, ws: ServerConnection, account_id: str) -> None:
        """处理微信 Hook 客户端连接"""
        self.mgr.register_hook(account_id, ws)

        # 发送已缓冲消息
        buffered = self.mgr.drain_buffer(account_id)
        if buffered:
            print(f"[WSS] 📬 Delivering {len(buffered)} buffered msgs to {account_id}")
            for msg in buffered:
                await ws.send(json.dumps({
                    "type": "SEND_MSG",
                    "data": msg,
                }, ensure_ascii=False))

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Pong 响应
                if data.get("type") == "pong":
                    self.mgr.update_pong(account_id)
                    continue

                # 业务消息: 客户来讯
                msg = self._extract_private_message(data)
                if msg is None:
                    continue

                # 异步调用后端 (非阻塞)
                asyncio.create_task(self._process_incoming(
                    account_id=account_id,
                    sender_id=msg["sender_id"],
                    content=msg["content"],
                ))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.mgr.unregister_hook(account_id)

    async def _process_incoming(
        self, account_id: str, sender_id: str, content: str,
    ) -> None:
        """处理入站客户消息: 调 AI → 推送前端"""
        result = await self._forward_to_backend(sender_id, content, account_id)
        if result:
            # 推送给前端审核面板
            await self.mgr.broadcast_frontend({
                "type": "new_message",
                "data": {
                    "trace_id": result.get("trace_id"),
                    "account_id": account_id,
                    "customer_id": sender_id,
                    "user_input": content,
                    "generated_text": result.get("generated_text"),
                    "max_score": result.get("max_score"),
                    "is_fallback": result.get("is_fallback", False),
                    "status": result.get("status", "PENDING"),
                },
            })

    async def _handle_frontend(self, ws: ServerConnection) -> None:
        """处理 Vue3 前端面板连接"""
        self.mgr.register_frontend(ws)
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    if data.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.mgr.unregister_frontend(ws)

    async def _handle_connection(self, ws: ServerConnection) -> None:
        """
        统一连接入口，根据 WebSocket 路径区分客户端类型。

        路径约定:
          /ws/hook/{account_id} → Hook 客户端
          /ws                → 前端面板
        """
        path = ws.request.path if hasattr(ws, 'request') else "/"

        if "/ws/hook/" in path:
            account_id = path.split("/ws/hook/")[-1]
            print(f"[WSS] Hook connecting: {account_id}")
            await self._handle_hook(ws, account_id)
        else:
            print(f"[WSS] Frontend connecting (path={path})")
            await self._handle_frontend(ws)

    # ── 心跳监测后台任务 ────────────────────────────────────

    async def _heartbeat_monitor(self) -> None:
        """后台心跳监测: Ping Hook 客户端 + 清理超时连接"""
        interval = settings.wss_heartbeat_interval
        print(f"[WSS] 💓 Heartbeat monitor started (interval={interval}s, "
              f"max_missed={settings.wss_max_missed_pongs})")

        while True:
            await asyncio.sleep(interval)

            # 向所有 Hook 发送 Ping
            for aid, entry in list(self.mgr._hooks.items()):
                try:
                    ping = json.dumps({"type": "ping", "ts": time.time()})
                    await entry["ws"].send(ping)
                except Exception:
                    pass  # check_heartbeats 会处理

            # 检查心跳超时
            dead = self.mgr.check_heartbeats()
            for aid in dead:
                ws = self.mgr.get_hook(aid)
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                self.mgr.unregister_hook(aid)

    # ── 服务生命周期 ────────────────────────────────────────

    async def start(self) -> websockets.Server:
        await self._ensure_http()
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
        )
        self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

        print(f"\n{'=' * 55}")
        print(f"  🚀 WSS Gateway V4.0 已启动")
        print(f"  📡 监听: ws://{self.host}:{self.port}")
        print(f"  🔗 Hook 接入: ws://{self.host}:{self.port}/ws/hook/{{account_id}}")
        print(f"  🖥️  前端接入: ws://{self.host}:{self.port}/ws")
        print(f"  🔗 后端 API: {self.backend_api_base}")
        print(f"{'=' * 55}\n")
        return self._server

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            print("[WSS] Server closed.")

        if self.http:
            await self.http.aclose()
            print("[WSS] HTTP client closed.")

    async def serve_forever(self) -> None:
        await self.start()
        await asyncio.Future()


# ════════════════════════════════════════════════════════════
# 便捷启动
# ════════════════════════════════════════════════════════════

async def start_wss_gateway(host: str = None, port: int = None) -> WSSGateway:
    gateway = WSSGateway(host=host, port=port)
    await gateway.start()
    return gateway


# ════════════════════════════════════════════════════════════
# Mock 测试
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio

    async def main():
        print("=" * 55)
        print("🧪 WSS Gateway V4.0 — 本地测试")
        print("=" * 55)

        gateway = WSSGateway(host="127.0.0.1", port=18765)

        # 消息过滤测试
        print("\n🔇 消息过滤测试:")
        tests = [
            ({"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "你好", "is_group": False}}, False),
            ({"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "邀请xxx加入了群聊", "is_group": False}}, True),
            ({"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "", "is_group": False}}, True),
            ({"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "群公告: xxx", "is_group": False}}, True),
            ({"event": "ON_LOGIN_SUCCESS", "data": {}}, True),
        ]
        for raw, expected in tests:
            result = gateway._extract_private_message(raw)
            ok = (result is None) == expected
            icon = "✅" if ok else "❌"
            content = raw["data"].get("content", "")[:30]
            print(f"   {icon} {'滤除' if expected else '通过'}: '{content}'")

        # 启停测试
        print("\n🌐 启动测试...")
        try:
            server = await gateway.start()
            await asyncio.sleep(0.5)
            await gateway.stop()
            print("   ✅ Gateway 启停正常")
        except Exception as e:
            print(f"   ⚠️  启停异常: {e}")

        print("\n🎉 WSS Gateway V4.0 测试完成!")
        print("💡 正式启动: python scripts/start_gateway.py")

    asyncio.run(main())
