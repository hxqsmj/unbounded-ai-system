"""
WSS Gateway 一键启动脚本 (V4.1)

⚠ 注意: 生产环境网关已集成在 FastAPI 进程内（main.py 随应用启动，
发送回调由 main.py 自动注入 Redis Worker）。
本脚本仅用于**独立调试**网关（消息过滤/心跳/缓冲），
独立模式没有后端发送通道，审核采纳的消息不会真正发出。

用法:
  python scripts/start_gateway.py
  python scripts/start_gateway.py --host 0.0.0.0 --port 8765

启动后:
  - Hook 客户端接入: ws://{host}:{port}/ws/hook/{account_id}?token=xxx
  - 前端面板接入:   ws://{host}:{port}/ws?token=xxx
  （API_TOKEN 配置后连接必须携带 token，否则被拒绝）
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.wss_gateway import WSSGateway
from app.core.config import settings


async def main():
    parser = argparse.ArgumentParser(description="WSS Gateway 一键启动")
    parser.add_argument("--host", default=settings.wss_host)
    parser.add_argument("--port", type=int, default=settings.wss_port)
    args = parser.parse_args()

    gateway = WSSGateway(
        host=args.host,
        port=args.port,
    )

    try:
        await gateway.start()
        print(f"✅ 网关运行中... 按 Ctrl+C 停止\n")
        await asyncio.Future()  # 永久运行
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号...")
    finally:
        await gateway.stop()
        print("👋 Gateway 已关闭")


if __name__ == "__main__":
    asyncio.run(main())
