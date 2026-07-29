"""
P1 Mock 微信 Hook 客户端 (V3.0)

模拟底层微信 Hook 向 WSS 网关推送客户消息，触发完整 AI 回复链路。

两种模式:
  1. WSS 模式: 连接 ws://localhost:8765 发送标准 Hook 报文
  2. API 模式: 直接 POST 后端 API (更可靠，跳过网关)

用法:
  python scripts/mock_hook_client.py          # 默认 API 模式
  python scripts/mock_hook_client.py --wss    # WSS 网关模式
  python scripts/mock_hook_client.py --interval 3  # 自定义发送间隔(秒)
"""

import asyncio
import json
import sys
import time
import argparse
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings

# ── 测试消息 ─────────────────────────────────────────────────
MOCK_MESSAGES = [
    {
        "account_id": "sales_01",
        "customer_id": "wx_client_嘉兴食品厂李总",
        "user_message": "你好，我们食品厂冷库要做地坪，大概2000平，推荐什么材料？",
    },
    {
        "account_id": "sales_01",
        "customer_id": "wx_client_电子厂王工",
        "user_message": "SMT车间防静电地坪的电阻标准是多少？施工流程大概多久？",
    },
    {
        "account_id": "sales_02",
        "customer_id": "wx_client_学校后勤张老师",
        "user_message": "学校旧水泥篮球场想翻新成硅PU的，标准场地多少钱？",
    },
    {
        "account_id": "sales_02",
        "customer_id": "wx_client_物业赵经理",
        "user_message": "地下车库旧环氧起皮脱落了，能翻新成固化地坪吗？",
    },
    {
        "account_id": "sales_03",
        "customer_id": "wx_client_冷链仓库周老板",
        "user_message": "冷库的地坪要求耐零下40度，水性聚氨酯能做吗？厚度选多少？",
    },
    {
        "account_id": "sales_03",
        "customer_id": "wx_client_酒店装修何设计",
        "user_message": "酒店大堂想做环氧彩砂，装饰性要好，你们有案例吗？",
    },
    {
        "account_id": "sales_02",
        "customer_id": "wx_client_化工厂陆主任",
        "user_message": "车间地面经常有酸碱液洒出来，哪种地坪耐腐蚀最好？",
    },
    {
        "account_id": "sales_01",
        "customer_id": "wx_client_幼儿园方园长",
        "user_message": "幼儿园教室想铺环保防摔的地板，你们有什么推荐的？",
    },
]


class Colors:
    """终端彩色输出"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'


# ═══════════════════════════════════════════════════════════
# 模式 1: 直接 API 调用 (推荐)
# ═══════════════════════════════════════════════════════════

async def api_mode(interval: float):
    """直接 POST 后端 API，最可靠的联调方式"""
    api_url = f"http://localhost:{settings.api_port}"

    print(f"{Colors.CYAN}📡 API 模式: {api_url}/api/v1/chat/generate{Colors.END}")
    print(f"   间隔: {interval}s | 消息数: {len(MOCK_MESSAGES)}\n")

    async with httpx.AsyncClient(timeout=60, base_url=api_url) as client:
        for i, msg in enumerate(MOCK_MESSAGES):
            print(f"{Colors.BLUE}[{i+1}/{len(MOCK_MESSAGES)}]{Colors.END} 📤 "
                  f"{msg['customer_id']}: {msg['user_message'][:50]}...")

            resp = await client.post("/api/v1/chat/generate", json={
                "account_id": msg["account_id"],
                "customer_id": msg["customer_id"],
                "user_message": msg["user_message"],
                "history": [],
            })

            if resp.status_code == 200:
                data = resp.json()
                icon = "✅" if not data["is_fallback"] else "⚠️"
                print(f"   {icon} Trace: {data['trace_id']} | Score: {data['max_score']:.4f} | "
                      f"Reply: {data['generated_text'][:60]}...")
            else:
                print(f"   ❌ HTTP {resp.status_code}: {resp.text[:100]}")

            if i < len(MOCK_MESSAGES) - 1:
                print(f"   ⏳ 等待 {interval}s...")
                await asyncio.sleep(interval)

    print(f"\n{Colors.GREEN}✅ API 模式完成！请打开前端 http://localhost:5173 查看待审核队列。{Colors.END}")


# ═══════════════════════════════════════════════════════════
# 模式 2: WSS 网关模式
# ═══════════════════════════════════════════════════════════

async def wss_mode(interval: float):
    """通过 WebSocket 连接 WSS 网关，模拟真实微信 Hook 推送"""
    try:
        import websockets
    except ImportError:
        print(f"{Colors.YELLOW}⚠️  websockets 库未安装，回退到 API 模式{Colors.END}")
        return await api_mode(interval)

    wss_url = f"ws://{settings.wss_host}:{settings.wss_port}"

    print(f"{Colors.CYAN}🔗 WSS 模式: {wss_url}{Colors.END}")
    print(f"   间隔: {interval}s | 消息数: {len(MOCK_MESSAGES)}\n")

    try:
        async with websockets.connect(wss_url) as ws:
            print(f"{Colors.GREEN}✅ WSS 已连接{Colors.END}\n")

            for i, msg in enumerate(MOCK_MESSAGES):
                # 构造标准微信 Hook 报文
                payload = json.dumps({
                    "event": "ON_RECV_MSG",
                    "data": {
                        "from_user": msg["customer_id"],
                        "to_user": msg["account_id"],
                        "content": msg["user_message"],
                        "is_group": False,
                        "msg_id": f"msg_{i:03d}",
                        "timestamp": time.time(),
                    },
                }, ensure_ascii=False)

                await ws.send(payload)
                print(f"{Colors.BLUE}[{i+1}/{len(MOCK_MESSAGES)}]{Colors.END} 📤 "
                      f"{msg['customer_id']}: {msg['user_message'][:50]}...")

                if i < len(MOCK_MESSAGES) - 1:
                    await asyncio.sleep(interval)

        print(f"\n{Colors.GREEN}✅ WSS 模式完成！{Colors.END}")

    except ConnectionRefusedError:
        print(f"{Colors.YELLOW}⚠️  WSS 网关未启动 ({wss_url})，回退到 API 模式{Colors.END}")
        return await api_mode(interval)
    except Exception as e:
        print(f"{Colors.YELLOW}⚠️  WSS 异常: {e}，回退到 API 模式{Colors.END}")
        return await api_mode(interval)


# ═══════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="无界AI · Mock 微信 Hook 客户端 (V3.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/mock_hook_client.py              # API 模式 (推荐)
  python scripts/mock_hook_client.py --wss        # WSS 网关模式
  python scripts/mock_hook_client.py -i 3         # 自定义间隔 3 秒
        """,
    )
    parser.add_argument("--wss", action="store_true", help="使用 WSS 网关模式 (默认 API 模式)")
    parser.add_argument("-i", "--interval", type=float, default=5.0, help="消息发送间隔 (秒, 默认 5s)")
    args = parser.parse_args()

    print(f"{'=' * 60}")
    print(f"🧪 无界AI · Mock 微信 Hook 客户端 (V3.0)")
    print(f"{'=' * 60}")
    print(f"  模式: {'WSS 网关' if args.wss else 'API 直连'}")
    print(f"  后端: http://localhost:{settings.api_port}")
    print()

    if args.wss:
        asyncio.run(wss_mode(args.interval))
    else:
        asyncio.run(api_mode(args.interval))
