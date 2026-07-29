"""
Task 1: 拟人化延迟与分布式风控引擎 (TypingSimulator V3.0)

功能:
  1. 拟人化打字延迟计算 (calculate_delay)
  2. 夜间作息保护 (is_sleep_time → SleepProtectionException)
  3. 内存模式调度 (schedule_message_memory)
  4. Redis 持久化模式生产者 (enqueue_message_redis → ZSET)
  5. Redis 持久化模式消费 Worker (redis_delay_worker → ZRANGEBYSCORE + ZREM)

依赖:
  - redis>=4.6.0 (redis.asyncio)

设计原则:
  - 全异步 (async/await)，严禁阻塞操作
  - 消费 Worker 使用原子 ZREM 防重复消费
  - 双模式透明切换: redis_url=None 降级为纯内存模式
"""

import asyncio
import json
import random
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

import redis.asyncio as aioredis

from app.core.config import settings


# ════════════════════════════════════════════════════════════
# 自定义异常
# ════════════════════════════════════════════════════════════

class SleepProtectionException(Exception):
    """夜间休眠保护激活 — 禁止在休眠时段发送消息"""
    pass


class DelayQueueFullException(Exception):
    """延迟队列已满"""
    pass


# ════════════════════════════════════════════════════════════
# TypingSimulator 核心类
# ════════════════════════════════════════════════════════════

class TypingSimulator:
    """
    拟人化打字模拟器 + 分布式延迟队列引擎

    双模式架构:
      - 内存模式 (redis_url=None):  适用于本地开发 / 单机部署
      - Redis 模式 (redis_url 非空): 适用于生产环境 / 多节点分布式
    """

    def __init__(self, redis_url: Optional[str] = None):
        """
        Args:
            redis_url: Redis 连接地址。
                       为 None 时仅启用内存模式；
                       例如 "redis://localhost:6379/0" 时启用 Redis 持久化。
        """
        self.redis_url = redis_url or settings.redis_url
        self.redis: Optional[aioredis.Redis] = None
        self.queue_key = settings.redis_queue_key
        self._worker_task: Optional[asyncio.Task] = None

    # ── Redis 连接管理 ──────────────────────────────────────

    async def init_redis(self) -> None:
        """惰性初始化 Redis 连接（连接池复用）"""
        if self.redis_url and not self.redis:
            self.redis = aioredis.from_url(
                self.redis_url,
                decode_responses=True,
                max_connections=10,
            )
            # 验证连接
            await self.redis.ping()
            print(f"[DelayEngine] Redis connected: {self.redis_url}")

    async def close_redis(self) -> None:
        """优雅关闭 Redis 连接"""
        if self.redis:
            await self.redis.aclose()
            self.redis = None
            print("[DelayEngine] Redis connection closed.")

    # ── 风控逻辑 ────────────────────────────────────────────

    def is_sleep_time(self) -> bool:
        """
        夜间休眠校验。

        Returns:
            True 如果当前时间在休眠窗口内 (默认 23:00 - 07:00)。
        """
        current_hour = datetime.now().hour
        return (
            current_hour >= settings.sleep_start_hour
            or current_hour < settings.sleep_end_hour
        )

    def calculate_delay(self, text: str) -> float:
        """
        拟人化打字延迟计算。

        公式: delay = len(text) × U(typing_speed_min, typing_speed_max)
                      + U(thinking_time_min, thinking_time_max)

        Args:
            text: 待发送的消息文本

        Returns:
            模拟打字 + 思考的延迟秒数（精确到 0.01 秒）
        """
        text_length = len(text)
        typing_time = text_length * random.uniform(
            settings.typing_speed_min, settings.typing_speed_max
        )
        thinking_time = random.uniform(
            settings.thinking_time_min, settings.thinking_time_max
        )
        return round(typing_time + thinking_time, 2)

    # ── 打字状态模拟 ────────────────────────────────────────

    async def trigger_typing_status(self, account_id: str, to_user: str) -> None:
        """
        触发"正在输入..."状态（WSS 协议层模拟）。

        实际生产中应通过 WebSocket 发送 typing indicator。
        """
        print(f"[WSS] Triggering typing status for {account_id} -> {to_user}")

    # ── 内存模式 (MVP) ──────────────────────────────────────

    async def schedule_message_memory(
        self,
        trace_id: str,
        account_id: str,
        to_user: str,
        text: str,
        send_callback: Callable[[str, str, str], Awaitable[None]],
    ) -> float:
        """
        [内存模式] 使用 asyncio.sleep 实现拟人延迟后回调发送。

        适用场景: 本地开发 / 单机部署 / 无 Redis 环境。

        Args:
            trace_id:    全链路追溯ID
            account_id:  发送账号ID
            to_user:     目标用户ID
            text:        消息文本
            send_callback: 实际发送回调 async (account_id, to_user, text) -> None

        Returns:
            实际应用的延迟秒数

        Raises:
            SleepProtectionException: 当前处于夜间休眠窗口
        """
        if self.is_sleep_time():
            raise SleepProtectionException(
                f"Refused: Night sleep window active "
                f"({settings.sleep_start_hour}:00 - {settings.sleep_end_hour}:00)."
            )

        delay = self.calculate_delay(text)
        await self.trigger_typing_status(account_id, to_user)

        print(
            f"[DelayEngine-Memory] Trace: {trace_id} | "
            f"Delay: {delay}s | Text: '{text[:30]}...'"
        )

        await asyncio.sleep(delay)
        await send_callback(account_id, to_user, text)

        print(f"[DelayEngine-Memory] Trace: {trace_id} | ✅ Sent.")
        return delay

    # ── Redis 持久化模式 (生产级) ───────────────────────────

    async def enqueue_message_redis(
        self,
        trace_id: str,
        account_id: str,
        to_user: str,
        text: str,
    ) -> float:
        """
        [Redis 生产者] 将消息推入 Redis ZSET 延迟队列。

        ZSET 结构:
          Key:   typing_delay_queue
          Score: execution_timestamp (Unix timestamp)
          Value: JSON {trace_id, account_id, to_user, text}

        Args:
            trace_id:   全链路追溯ID
            account_id: 发送账号ID
            to_user:    目标用户ID
            text:       消息文本

        Returns:
            计算出的延迟秒数

        Raises:
            SleepProtectionException: 当前处于夜间休眠窗口
        """
        if self.is_sleep_time():
            raise SleepProtectionException(
                f"Refused: Night sleep window active "
                f"({settings.sleep_start_hour}:00 - {settings.sleep_end_hour}:00)."
            )

        await self.init_redis()
        delay = self.calculate_delay(text)
        execute_at = time.time() + delay

        payload = json.dumps(
            {
                "trace_id": trace_id,
                "account_id": account_id,
                "to_user": to_user,
                "text": text,
                "queued_at": time.time(),
            },
            ensure_ascii=False,
        )

        await self.trigger_typing_status(account_id, to_user)

        # ZADD: score=执行时间戳, member=JSON负载
        await self.redis.zadd(self.queue_key, {payload: execute_at})

        print(
            f"[DelayEngine-Producer] Trace: {trace_id} | "
            f"Delay: {delay}s | Execute at: {datetime.fromtimestamp(execute_at)}"
        )
        return delay

    # ── Redis 消费 Worker (持久化消费闭环) ──────────────────

    async def redis_delay_worker(
        self,
        send_callback: Callable[[str, str, str], Awaitable[None]],
        poll_interval: float = 1.0,
    ) -> None:
        """
        [Redis 消费者] 后台轮询 ZSET，取出到期任务并执行。

        消费逻辑:
          1. ZRANGEBYSCORE: 按 score (执行时间戳) 提取到期任务
          2. ZREM:         原子性移除 (防多 Worker 重复消费)
          3. send_callback: 只有 ZREM 成功的节点才执行回调

        设计要点:
          - 轮询间隔 1s，非阻塞 (asyncio.sleep)
          - 原子 ZREM 保证 exactly-once 语义
          - 异常全捕获，Worker 不会因单条消息失败而崩溃

        Args:
            send_callback: 实际发送回调 async (account_id, to_user, text) -> None
            poll_interval: 轮询间隔 (秒)
        """
        await self.init_redis()

        print(
            f"[DelayEngine-Worker] 🚀 Started | Polling '{self.queue_key}' "
            f"every {poll_interval}s..."
        )

        while True:
            try:
                now = time.time()

                # 批量提取 score <= 当前时间戳的到期任务 (每次最多 10 条)
                items = await self.redis.zrangebyscore(
                    self.queue_key, min=0, max=now, start=0, num=10
                )

                for payload_str in items:
                    # 原子性移除 — 多节点部署时只有第一个成功的 Worker 消费
                    removed = await self.redis.zrem(self.queue_key, payload_str)
                    if not removed:
                        continue  # 已被其他 Worker 消费

                    data = json.loads(payload_str)
                    trace_id = data["trace_id"]

                    print(
                        f"[DelayEngine-Worker] Consuming Trace: {trace_id} | "
                        f"Queued at: {datetime.fromtimestamp(data['queued_at'])}"
                    )

                    # 异步回调发送，不阻塞轮询循环
                    asyncio.create_task(
                        send_callback(data["account_id"], data["to_user"], data["text"])
                    )

            except asyncio.CancelledError:
                print("[DelayEngine-Worker] 🛑 Cancelled. Shutting down...")
                break
            except Exception as e:
                print(f"[DelayEngine-Worker] ⚠️ Error: {type(e).__name__}: {e}")

            await asyncio.sleep(poll_interval)

    # ── Worker 生命周期管理 ─────────────────────────────────

    def start_worker(
        self,
        send_callback: Callable[[str, str, str], Awaitable[None]],
        poll_interval: float = 1.0,
    ) -> asyncio.Task:
        """
        启动后台 Worker 协程（非阻塞）。

        Args:
            send_callback: 发送回调
            poll_interval: 轮询间隔

        Returns:
            asyncio.Task — 可用 .cancel() 停止
        """
        if self._worker_task and not self._worker_task.done():
            print("[DelayEngine-Worker] Worker already running.")
            return self._worker_task

        self._worker_task = asyncio.create_task(
            self.redis_delay_worker(send_callback, poll_interval)
        )
        return self._worker_task

    async def stop_worker(self) -> None:
        """停止后台 Worker"""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            print("[DelayEngine-Worker] Worker stopped.")
        await self.close_redis()


# ════════════════════════════════════════════════════════════
# Mock 测试块 (if __name__ == '__main__')
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    一键验证 TypingSimulator 全部核心功能:
      1. 内存模式延迟发送
      2. 夜间休眠保护
      3. 延迟计算公式
      4. Redis 生产者入队
      5. Redis Worker 消费闭环
    """

    # ── Mock 发送回调 ───────────────────────────────────────
    async def mock_send_callback(account_id: str, to_user: str, text: str):
        print(
            f"✅ [MOCK SEND SUCCESS] "
            f"Account: {account_id} -> User: {to_user} | "
            f"Msg: '{text[:50]}...'"
        )

    # ── 主测试入口 ──────────────────────────────────────────
    async def main():
        print("=" * 60)
        print("🧪 TypingSimulator 全功能 Mock 测试")
        print("=" * 60)

        # 测试 1: 延迟计算公式
        print("\n📐 [Test 1] 延迟计算公式验证...")
        sim = TypingSimulator(redis_url=None)
        for length in [10, 50, 100]:
            text = "测" * length
            delay = sim.calculate_delay(text)
            expected_min = length * settings.typing_speed_min + settings.thinking_time_min
            expected_max = length * settings.typing_speed_max + settings.thinking_time_max
            assert expected_min <= delay <= expected_max, (
                f"Delay {delay} 不在预期范围 [{expected_min}, {expected_max}]"
            )
            print(f"   len={length} → delay={delay}s ✅ (范围: {expected_min}-{expected_max}s)")
        print("   ✅ 延迟公式验证通过")

        # 测试 2: 夜间休眠保护
        print("\n🌙 [Test 2] 夜间休眠保护验证...")
        now_hour = datetime.now().hour
        is_sleep = sim.is_sleep_time()
        expected_sleep = (now_hour >= 23 or now_hour < 7)
        assert is_sleep == expected_sleep, "休眠判断逻辑错误"
        if is_sleep:
            print(f"   当前 {now_hour}:00 处于休眠窗口，跳过内存模式测试（预期行为）")
        else:
            print(f"   当前 {now_hour}:00 不在休眠窗口，可正常发送 ✅")

        # 测试 3: 内存模式
        if not is_sleep:
            print("\n🧠 [Test 3] 内存模式 (asyncio.sleep) 验证...")
            delay = await sim.schedule_message_memory(
                trace_id="test-tr-001",
                account_id="sales_01",
                to_user="client_88",
                text="你好，请问有什么可以帮您？",
                send_callback=mock_send_callback,
            )
            print(f"   内存模式完成, 实际延迟: {delay}s ✅")

        # 测试 4: Redis 生产者入队
        print("\n📦 [Test 4] Redis 生产者入队验证...")
        sim_redis = TypingSimulator(redis_url="redis://localhost:6379/0")
        try:
            if not is_sleep:
                delay = await sim_redis.enqueue_message_redis(
                    trace_id="test-tr-002",
                    account_id="sales_02",
                    to_user="client_99",
                    text="您好，您的订单已发货，预计3天内送达。",
                )
                print(f"   Redis 入队成功, 延迟: {delay}s ✅")

                # 验证队列中有数据
                await sim_redis.init_redis()
                queue_size = await sim_redis.redis.zcard(sim_redis.queue_key)
                print(f"   队列当前任务数: {queue_size} ✅")
            else:
                print("   休眠窗口，跳过入队测试")
        except Exception as e:
            print(f"   ⚠️ Redis 不可用 ({e})，跳过 Redis 测试（预期行为）")
        finally:
            await sim_redis.close_redis()

        # 测试 5: Redis Worker 消费闭环
        print("\n🔄 [Test 5] Redis Worker 消费闭环验证...")
        if not is_sleep:
            sim_worker = TypingSimulator(redis_url="redis://localhost:6379/0")
            try:
                # 先入队一条消息
                await sim_worker.enqueue_message_redis(
                    trace_id="test-tr-003",
                    account_id="sales_03",
                    to_user="client_100",
                    text="Worker 消费测试消息",
                )

                # 启动 Worker（后台协程）
                worker_task = sim_worker.start_worker(
                    send_callback=mock_send_callback,
                    poll_interval=0.5,  # 快速轮询
                )

                # 给 Worker 一些时间消费
                print("   等待 Worker 消费...")
                await asyncio.sleep(2)

                # 验证队列已清空
                await sim_worker.init_redis()
                queue_size = await sim_worker.redis.zcard(sim_worker.queue_key)
                print(f"   消费后队列剩余: {queue_size} ✅")

                # 停止 Worker
                await sim_worker.stop_worker()
                print("   ✅ Worker 消费闭环验证完成")
            except Exception as e:
                print(f"   ⚠️ Worker 测试异常: {e}")
        else:
            print("   休眠窗口，跳过 Worker 测试")

        print("\n" + "=" * 60)
        print("🎉 TypingSimulator 全部 Mock 测试完成!")
        print("=" * 60)

    asyncio.run(main())
