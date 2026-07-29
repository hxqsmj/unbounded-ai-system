"""
微信消息桥接器 — 连接微信客户端与无界AI WSS 网关

工作原理:
  本桥接器作为中间层，连接微信 Hook 框架 (ComWeChatRobot) 与 WSS Gateway。
  - 从 Hook 框架拉取新消息 → 转为标准格式 → 推送到 WSS Gateway
  - 从 WSS Gateway 接收发送指令 → 调用 Hook 框架执行微信发送

支持的 Hook 框架:
  1. ComWeChatRobot (推荐) — 开源, 本地 HTTP API :5555
  2. 可爱猫 (iCat)         — HTTP Hook 框架
  3. SunnyNet / 千寻       — WebSocket Hook

用法:
  # 默认 (ComWeChatRobot 在本地 :5555)
  python scripts/wechat_bridge.py --account sales_01

  # 连接 WSS Gateway
  python scripts/wechat_bridge.py --account sales_01 --gateway ws://127.0.0.1:8765

  # 使用可爱猫框架
  python scripts/wechat_bridge.py --account sales_01 --driver icat --hook-url http://127.0.0.1:8090

前提:
  1. 微信已登录电脑
  2. ComWeChatRobot 已注入并启动 (或其他 Hook 框架)
  3. WSS Gateway 已启动 (python scripts/start_gateway.py)
"""

import asyncio
import json
import sys
import time
import argparse
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── 配置 ─────────────────────────────────────────────────────
MSG_SEEN = set()  # 已处理消息 ID，防重复


def print_log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m"}
    c = colors.get(level, "")
    print(f"{c}[{ts} {level}]\033[0m {msg}")


# ═══════════════════════════════════════════════════════════
# 驱动层: 适配不同 Hook 框架
# ═══════════════════════════════════════════════════════════

class ComWeChatRobotDriver:
    """
    ComWeChatRobot 驱动 (推荐)
    GitHub: https://github.com/ljc545w/ComWeChatRobot

    安装:
      1. 下载 Release 中的 DLL
      2. 用注入器将 DLL 注入微信进程
      3. 确认 HTTP API 在 http://127.0.0.1:5555 可用
    """

    def __init__(self, base_url: str = "http://127.0.0.1:5555"):
        self.base_url = base_url.rstrip("/")

    async def get_contacts(self) -> list:
        """获取最近联系人列表"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(f"{self.base_url}/api/getcontactlist")
            return resp.json().get("data", [])

    async def get_new_messages(self) -> list:
        """获取新消息"""
        async with httpx.AsyncClient(timeout=10) as c:
            try:
                resp = await c.post(f"{self.base_url}/api/getmessage")
                return resp.json().get("data", [])
            except Exception:
                return []

    async def send_text(self, wxid: str, text: str) -> bool:
        """发送文本消息"""
        async with httpx.AsyncClient(timeout=10) as c:
            try:
                resp = await c.post(f"{self.base_url}/api/sendtextmsg", json={
                    "wxid": wxid,
                    "msg": text,
                })
                return resp.status_code == 200
            except Exception as e:
                print_log("ERR", f"发送失败: {e}")
                return False


class ICatDriver:
    """
    可爱猫 (iCat) 驱动
    HTTP Hook 框架, 内置回调

    安装:
      1. 下载可爱猫框架并启动
      2. 在框架中登录微信
      3. 确认 HTTP API 可用
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8090"):
        self.base_url = base_url.rstrip("/")

    async def get_contacts(self) -> list:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{self.base_url}/v1/contacts")
            return resp.json().get("data", [])

    async def get_new_messages(self) -> list:
        async with httpx.AsyncClient(timeout=10) as c:
            try:
                resp = await c.get(f"{self.base_url}/v1/messages?type=new")
                return resp.json().get("data", [])
            except Exception:
                return []

    async def send_text(self, wxid: str, text: str) -> bool:
        async with httpx.AsyncClient(timeout=10) as c:
            try:
                resp = await c.post(f"{self.base_url}/v1/messages/send", json={
                    "to_wxid": wxid,
                    "content": text,
                    "type": "text",
                })
                return resp.status_code == 200
            except Exception as e:
                print_log("ERR", f"发送失败: {e}")
                return False


# ═══════════════════════════════════════════════════════════
# 桥接器核心
# ═══════════════════════════════════════════════════════════

class WeChatBridge:
    """
    微信 ↔ WSS Gateway 双向桥接器。

    ┌──────────┐    HTTP     ┌──────────┐    WSS     ┌──────────┐
    │ 微信Hook  │ ──────────▶ │  Bridge  │ ────────▶ │ Gateway  │
    │ (本地API) │ ◀────────── │ (本脚本) │ ◀──────── │  :8765   │
    └──────────┘    HTTP     └──────────┘    WSS     └──────────┘
    """

    def __init__(self, account_id: str, driver, gateway_url: str = "ws://127.0.0.1:8765"):
        self.account_id = account_id
        self.driver = driver
        self.gateway_url = gateway_url
        self.ws: Optional[websockets.ClientConnection] = None
        self._running = False

    async def start(self):
        """启动桥接器"""
        print_log("INFO", f"🚀 微信桥接器启动")
        print_log("INFO", f"   账号: {self.account_id}")
        print_log("INFO", f"   网关: {self.gateway_url}/ws/hook/{self.account_id}")

        self._running = True

        # 连接 WSS Gateway
        await self._connect_gateway()

        # 并发: 拉取消息 + 监听发送指令
        await asyncio.gather(
            self._poll_messages(),
            self._listen_commands(),
        )

    async def _connect_gateway(self):
        """连接 WSS Gateway，带重试"""
        url = f"{self.gateway_url}/ws/hook/{self.account_id}"
        retries = 0
        while self._running:
            try:
                self.ws = await websockets.connect(url)
                print_log("OK", f"✅ 已连接 WSS Gateway: {url}")

                # 发送在线通知
                await self.ws.send(json.dumps({
                    "event": "ON_LOGIN_SUCCESS",
                    "data": {"account_id": self.account_id, "ts": time.time()},
                }, ensure_ascii=False))

                retries = 0
                return
            except Exception as e:
                retries += 1
                delay = min(retries * 2, 30)
                print_log("WARN", f"网关连接失败 (重试 {retries}, {delay}s后): {e}")
                await asyncio.sleep(delay)

    async def _poll_messages(self):
        """轮询微信新消息, 转发到网关"""
        print_log("INFO", f"📡 开始轮询微信消息 (每3秒)...")
        last_check = set()

        while self._running:
            try:
                msgs = await self.driver.get_new_messages()
                for msg in msgs:
                    msg_id = msg.get("msg_id") or msg.get("id") or hashlib.md5(
                        json.dumps(msg, sort_keys=True).encode()
                    ).hexdigest()[:16]

                    if msg_id in MSG_SEEN or msg_id in last_check:
                        continue

                    MSG_SEEN.add(msg_id)
                    last_check.add(msg_id)

                    # 提取关键字段 (兼容不同框架格式)
                    from_user = msg.get("from_user") or msg.get("sender") or msg.get("wxid", "")
                    content = msg.get("content") or msg.get("message") or msg.get("text", "")
                    is_group = msg.get("is_group") or msg.get("chatroom", False)

                    if not content or not from_user:
                        continue

                    # 推送到 WSS Gateway
                    await self._push_to_gateway(from_user, content, is_group)

                # 清理旧 ID (保留最近 500 个)
                if len(last_check) > 500:
                    last_check.clear()

            except Exception as e:
                print_log("ERR", f"消息轮询异常: {e}")

            await asyncio.sleep(3)

    async def _push_to_gateway(self, from_user: str, content: str, is_group: bool = False):
        """推送消息到 WSS Gateway"""
        if not self.ws:
            print_log("WARN", "网关未连接，跳过推送")
            return

        payload = json.dumps({
            "event": "ON_RECV_MSG",
            "data": {
                "from_user": from_user,
                "to_user": self.account_id,
                "content": content,
                "is_group": is_group,
                "msg_id": f"bridge_{int(time.time() * 1000)}",
                "timestamp": time.time(),
            },
        }, ensure_ascii=False)

        try:
            await self.ws.send(payload)
            print_log("OK", f"📤 新消息: {from_user[:20]} → {content[:40]}...")
        except Exception as e:
            print_log("ERR", f"推送失败: {e}")
            self.ws = None

    async def _listen_commands(self):
        """监听 WSS Gateway 的发送指令"""
        while self._running:
            if not self.ws:
                await asyncio.sleep(2)
                continue

            try:
                raw = await self.ws.recv()
                data = json.loads(raw)

                # Pong 心跳
                if data.get("type") == "ping":
                    await self.ws.send(json.dumps({"type": "pong", "ts": time.time()}))
                    continue

                # 发送指令: Gateway → Hook → 微信
                if data.get("type") == "SEND_MSG":
                    send_data = data.get("data", {})
                    to_user = send_data.get("to_user", "")
                    text = send_data.get("text", "")

                    if to_user and text:
                        ok = await self.driver.send_text(to_user, text)
                        if ok:
                            print_log("OK", f"📨 已发送: → {to_user[:20]} | '{text[:40]}...'")
                        else:
                            print_log("ERR", f"发送失败: → {to_user[:20]}")

            except websockets.exceptions.ConnectionClosed:
                print_log("WARN", "网关连接断开, 重连中...")
                self.ws = None
                await self._connect_gateway()
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print_log("ERR", f"指令监听异常: {e}")
                await asyncio.sleep(1)


# ═══════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="无界AI · 微信消息桥接器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用 ComWeChatRobot (默认)
  python scripts/wechat_bridge.py --account sales_01

  # 指定 Hook 框架和地址
  python scripts/wechat_bridge.py --account sales_01 --driver comwechat --hook-url http://127.0.0.1:5555

  # 连接远程 WSS Gateway
  python scripts/wechat_bridge.py --account sales_01 --gateway ws://0.tcp.ngrok.io:12345

驱动选项:
  comwechat  — ComWeChatRobot (开源, 推荐)
  icat       — 可爱猫 HTTP Hook 框架
        """,
    )
    parser.add_argument("--account", "-a", default="sales_01", help="销售账号 ID")
    parser.add_argument("--driver", "-d", default="comwechat", choices=["comwechat", "icat"])
    parser.add_argument("--hook-url", default=None, help="Hook 框架 HTTP API 地址")
    parser.add_argument("--gateway", "-g", default="ws://127.0.0.1:8765", help="WSS Gateway 地址")
    args = parser.parse_args()

    # 选择驱动
    if args.driver == "icat":
        url = args.hook_url or "http://127.0.0.1:8090"
        driver = ICatDriver(base_url=url)
    else:
        url = args.hook_url or "http://127.0.0.1:5555"
        driver = ComWeChatRobotDriver(base_url=url)

    bridge = WeChatBridge(
        account_id=args.account,
        driver=driver,
        gateway_url=args.gateway,
    )

    print(f"""
{'=' * 55}
  🤖 无界AI · 微信消息桥接器
{'=' * 55}
  驱动: {args.driver} ({url})
  账号: {args.account}
  网关: {args.gateway}/ws/hook/{args.account}
{'=' * 55}

💡 确保以下服务已启动:
  1. 微信已登录 + Hook 框架已注入
  2. WSS Gateway: python scripts/start_gateway.py
  3. FastAPI 后端: python -m uvicorn app.main:app --port 8001
""")

    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        print_log("INFO", "🛑 桥接器已停止")
