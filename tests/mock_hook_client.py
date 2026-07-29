"""
Mock Hook 客户端 — 模拟微信/企微 Hook 向 WSS Gateway 推送消息。

用途:
  1. 端到端测试 WSS Gateway 的消息接收与转发
  2. 验证心跳 Ping/Pong 机制
  3. 开发阶段无需真实微信客户端即可调试全链路

使用:
  python tests/mock_hook_client.py
"""

import asyncio
import json
import time
import random
import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import websockets

# ════════════════════════════════════════════════════════════
# Mock 消息模板
# ════════════════════════════════════════════════════════════

MOCK_MESSAGES = [
    {
        "event": "ON_RECV_MSG",
        "data": {
            "from_user": "wxid_customer_001",
            "to_user": "wxid_sales_01",
            "content": "你好，请问产品A的价格是多少？",
            "is_group": False,
            "msg_id": "msg_001",
            "timestamp": 0,
        },
    },
    {
        "event": "ON_RECV_MSG",
        "data": {
            "from_user": "wxid_customer_002",
            "to_user": "wxid_sales_01",
            "content": "你们的售后服务怎么样？",
            "is_group": False,
            "msg_id": "msg_002",
            "timestamp": 0,
        },
    },
    {
        "event": "ON_RECV_MSG",
        "data": {
            "from_user": "wxid_customer_001",
            "to_user": "wxid_sales_01",
            "content": "邀请xxx加入了群聊",
            "is_group": False,
            "msg_id": "msg_003",
            "timestamp": 0,
        },
    },
    {
        "event": "ON_RECV_MSG",
        "data": {
            "from_user": "wxid_system",
            "to_user": "wxid_sales_01",
            "content": "群公告：明天放假",
            "is_group": False,
            "msg_id": "msg_004",
            "timestamp": 0,
        },
    },
    {
        "event": "ON_RECV_MSG",
        "data": {
            "from_user": "wxid_customer_003",
            "to_user": "wxid_sales_01",
            "content": "在吗？我要退款",
            "is_group": False,
            "msg_id": "msg_005",
            "timestamp": 0,
        },
    },
]


# ════════════════════════════════════════════════════════════
# Mock Hook 客户端
# ════════════════════════════════════════════════════════════

class MockHookClient:
    """
    模拟微信 Hook 客户端的 WebSocket 行为。

    功能:
      - 连接 WSS Gateway
      - 按间隔发送模拟私信
      - 响应 Ping/Pong 心跳
      - 打印 Gateway 转发的 AI 回复
    """

    def __init__(self, gateway_url: str = "ws://127.0.0.1:8765"):
        self.gateway_url = gateway_url

    async def run(self, message_interval: float = 2.0):
        """
        启动 Mock 客户端。

        Args:
            message_interval: 每条消息之间的间隔（秒）
        """
        print(f"[MockHook] 🔗 Connecting to {self.gateway_url}...")

        try:
            async with websockets.connect(self.gateway_url) as ws:
                print(f"[MockHook] ✅ Connected!")

                # 启动心跳响应协程
                heartbeat_task = asyncio.create_task(self._respond_heartbeat(ws))

                # 发送模拟消息
                for i, msg_template in enumerate(MOCK_MESSAGES):
                    # 注入时间戳
                    msg = json.loads(json.dumps(msg_template))
                    msg["data"]["timestamp"] = time.time()

                    payload = json.dumps(msg, ensure_ascii=False)
                    await ws.send(payload)

                    content = msg["data"]["content"]
                    is_system = any(
                        kw in content
                        for kw in ["邀请", "群公告", "群聊"]
                    )

                    if is_system:
                        print(
                            f"[MockHook] 📤 Sent #{i+1}: 🔇 '{content[:40]}' "
                            f"(系统消息, 应被过滤)"
                        )
                    else:
                        print(
                            f"[MockHook] 📤 Sent #{i+1}: 💬 '{content[:40]}' "
                            f"from {msg['data']['from_user']}"
                        )

                    await asyncio.sleep(message_interval)

                # 再等一会儿看看有没有回复
                print("[MockHook] All messages sent. Waiting for responses...")
                await asyncio.sleep(3)

                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        except ConnectionRefusedError:
            print(
                f"[MockHook] ❌ Connection refused! "
                f"请先启动 WSS Gateway: python app/services/wss_gateway.py"
            )
        except Exception as e:
            print(f"[MockHook] ❌ Error: {type(e).__name__}: {e}")

        print("[MockHook] 🔌 Disconnected.")

    async def _respond_heartbeat(self, ws):
        """自动响应 Gateway 的 Ping"""
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    if data.get("type") == "ping":
                        pong = json.dumps({"type": "pong", "ts": time.time()})
                        await ws.send(pong)
                        print(f"[MockHook] 💓 Pong sent")
                except json.JSONDecodeError:
                    pass
        except asyncio.CancelledError:
            pass


# ════════════════════════════════════════════════════════════
# 直接运行
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Mock Hook Client — 模拟微信推流")
    print("=" * 60)
    print("确保 WSS Gateway 已启动: python app/services/wss_gateway.py")
    print()

    client = MockHookClient(gateway_url="ws://127.0.0.1:8765")
    asyncio.run(client.run(message_interval=1.5))
