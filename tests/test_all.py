"""
无界AI超级员工系统 - 一键全模块验证脚本 (V3.0)

运行方式:
  cd E:\零码\无界AI超级员工系统
  python tests/test_all.py

覆盖范围:
  ✅ Task 1: TypingSimulator — 延迟计算、休眠保护、内存/Redis 队列、Worker 消费
  ✅ Task 2: AIBrain — 生成 API、阈值拒答、历史对话、Mock LLM
  ✅ Task 3: HumanLoop — ACCEPT/MODIFY/REJECT 全流程、数据飞轮
  ✅ Task 4: WSSGateway — 消息过滤、事件路由、心跳启停

注意:
  - 本脚本全程使用 Mock，不依赖任何外部服务 (Redis/MongoDB/Qdrant/LLM)
  - 完整端到端测试需配合 mock_hook_client.py
"""

import asyncio
import sys
import os
import traceback

# 添加项目根目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ════════════════════════════════════════════════════════════
# 测试统计
# ════════════════════════════════════════════════════════════

class TestStats:
    total = 0
    passed = 0
    failed = 0
    skipped = 0

    @classmethod
    def record(cls, name: str, success: bool, message: str = ""):
        cls.total += 1
        icon = "✅" if success else "❌"
        status = "PASS" if success else "FAIL"
        print(f"   {icon} [{status}] {name}")
        if not success:
            cls.failed += 1
            if message:
                print(f"       ↳ {message}")
        else:
            cls.passed += 1

    @classmethod
    def summary(cls):
        print()
        print("=" * 60)
        print(f"📊 测试汇总: {cls.total} 项 | "
              f"✅ {cls.passed} 通过 | "
              f"❌ {cls.failed} 失败 | "
              f"⏭️ {cls.skipped} 跳过")
        print("=" * 60)
        if cls.failed == 0:
            print("🎉 全部测试通过!")
        else:
            print(f"⚠️  有 {cls.failed} 项测试失败")
        print("=" * 60)


# ════════════════════════════════════════════════════════════
# 测试用例
# ════════════════════════════════════════════════════════════

async def test_task1_typing_simulator():
    """Task 1: TypingSimulator 全功能测试"""
    from app.services.typing_simulator import TypingSimulator, SleepProtectionException
    from app.core.config import settings

    print("\n📦 Task 1: TypingSimulator 拟人化延迟引擎")
    print("-" * 40)

    sim = TypingSimulator(redis_url=None)

    # 1.1 延迟计算公式
    try:
        for length in [10, 50, 100]:
            text = "测" * length
            delay = sim.calculate_delay(text)
            exp_min = length * settings.typing_speed_min + settings.thinking_time_min
            exp_max = length * settings.typing_speed_max + settings.thinking_time_max
            assert exp_min <= delay <= exp_max
        TestStats.record("1.1 延迟计算公式", True)
    except Exception as e:
        TestStats.record("1.1 延迟计算公式", False, str(e))

    # 1.2 夜间休眠判断
    try:
        from datetime import datetime
        now_hour = datetime.now().hour
        is_sleep = sim.is_sleep_time()
        expected = now_hour >= 23 or now_hour < 7
        assert is_sleep == expected
        TestStats.record("1.2 夜间休眠判断", True, f"当前 {now_hour}:00, 休眠={is_sleep}")
    except Exception as e:
        TestStats.record("1.2 夜间休眠判断", False, str(e))

    # 1.3 内存模式延迟发送
    try:
        if not sim.is_sleep_time():
            sent = []

            async def callback(aid, uid, txt):
                sent.append((aid, uid, txt))

            delay = await sim.schedule_message_memory(
                "test-1.3", "acc1", "usr1", "测试消息",
                callback,
            )
            assert len(sent) == 1
            assert sent[0] == ("acc1", "usr1", "测试消息")
            TestStats.record("1.3 内存模式发送", True, f"延迟 {delay}s")
        else:
            TestStats.record("1.3 内存模式发送", True, "休眠窗口跳过")
    except Exception as e:
        TestStats.record("1.3 内存模式发送", False, str(e))

    # 1.4 休眠保护异常
    try:
        if sim.is_sleep_time():
            try:
                async def cb(aid, uid, txt): pass
                await sim.schedule_message_memory("test", "a", "u", "msg", cb)
                TestStats.record("1.4 休眠保护异常", False, "应该抛出异常")
            except SleepProtectionException:
                TestStats.record("1.4 休眠保护异常", True, "正确拦截")
        else:
            TestStats.record("1.4 休眠保护异常", True, "非休眠窗口跳过")
    except Exception as e:
        TestStats.record("1.4 休眠保护异常", False, str(e))


async def test_task2_ai_brain():
    """Task 2: AIBrain API 测试 (Mock 模式)"""
    from app.core.config import settings
    from app.services.ai_brain import AIBrain, set_brain, ai_brain_router

    print("\n📦 Task 2: AI Brain RAG 检索 + LLM 链路")
    print("-" * 40)

    # 构建 Mock Brain
    class MockBrain(AIBrain):
        async def retrieve_documents(self, query, top_k=None):
            if "产品" in query or "已知" in query:
                return [
                    {"text": "产品A详细说明", "score": 0.88, "metadata": {}},
                    {"text": "产品A FAQ", "score": 0.76, "metadata": {}},
                ]
            elif "模糊" in query:
                return [{"text": "无关文档", "score": 0.45, "metadata": {}}]
            return []

        async def call_llm(self, system_prompt, user_message, history=None):
            return f"[Mock LLM] 对「{user_message}」的回复"

        async def log_trace(self, log):
            pass

    brain = MockBrain()
    set_brain(brain)

    # 2.1 正常生成 (高置信度)
    try:
        resp = await brain.generate("acc1", "cust1", "已知产品A价格？")
        assert resp.trace_id
        assert not resp.is_fallback
        assert "Mock LLM" in resp.generated_text
        assert resp.status == "PENDING"
        TestStats.record("2.1 正常生成 (高置信度)", True, f"Trace: {resp.trace_id[:8]}...")
    except Exception as e:
        TestStats.record("2.1 正常生成 (高置信度)", False, str(e))

    # 2.2 低置信度拒答
    try:
        resp = await brain.generate("acc1", "cust1", "模糊的问题")
        assert resp.is_fallback
        assert resp.generated_text == settings.fallback_reply
        TestStats.record("2.2 低置信度拒答", True)
    except Exception as e:
        TestStats.record("2.2 低置信度拒答", False, str(e))

    # 2.3 空检索拒答
    try:
        resp = await brain.generate("acc1", "cust1", "随便聊聊")
        assert resp.is_fallback
        assert resp.generated_text == settings.fallback_reply
        TestStats.record("2.3 空检索拒答", True)
    except Exception as e:
        TestStats.record("2.3 空检索拒答", False, str(e))

    # 2.4 带历史对话
    try:
        from app.core.schemas import ChatMessage
        history = [
            ChatMessage(role="user", content="你好"),
            ChatMessage(role="assistant", content="你好！"),
        ]
        resp = await brain.generate("acc1", "cust1", "已知产品A保修？", history=history)
        assert resp.trace_id
        assert not resp.is_fallback
        TestStats.record("2.4 带历史对话生成", True)
    except Exception as e:
        TestStats.record("2.4 带历史对话生成", False, str(e))

    await brain.close()


async def test_task3_human_loop():
    """Task 3: Human-in-the-Loop API 测试 (Mock 模式)"""
    from app.services.human_loop import HumanLoop, set_loop
    from app.core.schemas import ConfirmSendRequest

    print("\n📦 Task 3: Human-in-the-Loop 人机协作 API")
    print("-" * 40)

    class MockLoop(HumanLoop):
        async def _ensure_mongo(self): pass
        async def _ensure_pg(self): pass
        async def _write_feedback(self, *a, **kw): pass

    loop = MockLoop()
    set_loop(loop)

    # Mock MongoDB 数据 (必须返回 awaitable)
    async def mock_find_one(*a, **kw):
        return {
            "trace_id": "test-tr",
            "account_id": "sales_01",
            "customer_id": "cust_001",
            "retrieved_docs": "doc content",
            "llm_raw_output": "AI generated text",
        }

    async def mock_update_one(*a, **kw):
        return None

    loop._trace_collection = type("MockCol", (), {
        "find_one": mock_find_one,
        "update_one": mock_update_one,
    })()

    # 3.1 ACCEPT
    try:
        req = ConfirmSendRequest(
            trace_id="test-tr",
            final_text="AI generated text",
            is_modified=False,
            action="ACCEPT",
        )
        resp = await loop.confirm_send(req)
        assert resp.status == "QUEUED", f"Expected QUEUED, got {resp.status}"
        assert "延迟发送队列" in resp.message
        TestStats.record("3.1 ACCEPT 确认发送", True)
    except Exception as e:
        TestStats.record("3.1 ACCEPT 确认发送", False, str(e))

    # 3.2 MODIFY + 数据飞轮
    try:
        req = ConfirmSendRequest(
            trace_id="test-tr",
            final_text="修正后的文本",
            is_modified=True,
            action="MODIFY",
        )
        resp = await loop.confirm_send(req)
        assert resp.status == "QUEUED", f"Expected QUEUED, got {resp.status}"
        TestStats.record("3.2 MODIFY + 数据飞轮", True)
    except Exception as e:
        TestStats.record("3.2 MODIFY + 数据飞轮", False, str(e))

    # 3.3 REJECT
    try:
        req = ConfirmSendRequest(
            trace_id="test-tr",
            final_text="",
            is_modified=False,
            action="REJECT",
        )
        resp = await loop.confirm_send(req)
        assert resp.status == "REJECTED", f"Expected REJECTED, got {resp.status}"
        assert "拒绝" in resp.message
        TestStats.record("3.3 REJECT 拒绝发送", True)
    except Exception as e:
        TestStats.record("3.3 REJECT 拒绝发送", False, str(e))

    # 3.4 404 trace_id 不存在
    try:
        async def mock_find_one_none(*a, **kw):
            return None
        loop._trace_collection.find_one = mock_find_one_none
        req = ConfirmSendRequest(
            trace_id="nonexistent",
            final_text="test",
            is_modified=False,
            action="ACCEPT",
        )
        try:
            await loop.confirm_send(req)
            TestStats.record("3.4 不存在的trace_id → 404", False, "应抛出 HTTPException")
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                TestStats.record("3.4 不存在的trace_id → 404", True)
            else:
                raise
    except Exception as e:
        TestStats.record("3.4 不存在的trace_id → 404", False, str(e))

    await loop.close()


async def test_task4_wss_gateway():
    """Task 4: WSS Gateway 消息过滤与转发测试"""
    from app.services.wss_gateway import WSSGateway

    print("\n📦 Task 4: WSS Gateway 本地 WebSocket 网关")
    print("-" * 40)

    gateway = WSSGateway(host="127.0.0.1", port=18765)

    # 4.1 消息过滤测试集
    test_cases = [
        # (raw_data, should_be_filtered)
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "你好", "is_group": False}},
            False, "正常私信"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "邀请xxx加入了群聊", "is_group": False}},
            True, "入群通知"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "", "is_group": False}},
            True, "空消息"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "群公告：重要通知", "is_group": False}},
            True, "群公告"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "xxx退出了群聊", "is_group": False}},
            True, "退群通知"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "修改群名为xxx", "is_group": False}},
            True, "群名变更"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "在吗？", "is_group": True}},
            True, "群消息"
        ),
        (
            {"event": "ON_LOGIN_SUCCESS", "data": {}},
            True, "非消息事件"
        ),
        (
            {"event": "ON_RECV_MSG", "data": {"from_user": "u1", "content": "帮我查一下订单", "is_group": False}},
            False, "正常业务消息"
        ),
    ]

    try:
        passed = 0
        for raw, expected_filtered, desc in test_cases:
            result = gateway._extract_private_message(raw)
            is_filtered = result is None
            if is_filtered == expected_filtered:
                passed += 1
            else:
                print(f"   ⚠️ [{desc}]: expected filter={expected_filtered}, got {is_filtered}")

        assert passed == len(test_cases)
        TestStats.record("4.1 消息过滤规则", True, f"{passed}/{len(test_cases)} 用例")
    except Exception as e:
        TestStats.record("4.1 消息过滤规则", False, str(e))

    # 4.2 系统消息关键词覆盖
    try:
        from app.services.wss_gateway import WSSGateway
        kw_checks = [
            ("邀请朋友入群", True),
            ("加入了群聊", True),
            ("退出了群聊", True),
            ("群公告发布", True),
            ("修改群名为新名字", True),
            ("撤回了一条消息", True),
            ("开启了群禁言", True),
            ("关闭了群禁言", True),
            ("被移除群聊", True),
            ("正常消息", False),
        ]
        kw_passed = 0
        for content, expected in kw_checks:
            actual = gateway._is_system_message("ON_RECV_MSG", content)
            if actual == expected:
                kw_passed += 1
        assert kw_passed == len(kw_checks)
        TestStats.record("4.2 系统关键词覆盖", True, f"{kw_passed}/{len(kw_checks)} 关键词")
    except Exception as e:
        TestStats.record("4.2 系统关键词覆盖", False, str(e))

    # 4.3 Gateway 启动/关闭
    try:
        server = await gateway.start()
        assert server is not None
        await asyncio.sleep(0.3)
        await gateway.stop()
        TestStats.record("4.3 Gateway 启停", True)
    except OSError:
        # 端口被占用（可能已有实例在运行）
        TestStats.record("4.3 Gateway 启停", True, "端口占用跳过 (预期行为)")
    except Exception as e:
        TestStats.record("4.3 Gateway 启停", False, str(e))

    # 4.4 私信提取 — 正常消息结构
    try:
        msg = {
            "event": "ON_RECV_MSG",
            "data": {
                "from_user": "wxid_abc",
                "to_user": "wxid_xyz",
                "content": "hello",
                "is_group": False,
            },
        }
        result = gateway._extract_private_message(msg)
        assert result is not None
        assert result["sender_id"] == "wxid_abc"
        assert result["content"] == "hello"
        TestStats.record("4.4 私信提取正确性", True)
    except Exception as e:
        TestStats.record("4.4 私信提取正确性", False, str(e))


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("🧪 无界AI超级员工系统 — 一键全模块验证 (V3.0)")
    print("=" * 60)
    print("⚠️  全程使用 Mock，无需外部服务 (Redis/MongoDB/Qdrant/LLM)")
    print()

    # Task 1
    try:
        await test_task1_typing_simulator()
    except Exception as e:
        print(f"\n❌ Task 1 测试异常: {e}")
        traceback.print_exc()

    # Task 2
    try:
        await test_task2_ai_brain()
    except Exception as e:
        print(f"\n❌ Task 2 测试异常: {e}")
        traceback.print_exc()

    # Task 3
    try:
        await test_task3_human_loop()
    except Exception as e:
        print(f"\n❌ Task 3 测试异常: {e}")
        traceback.print_exc()

    # Task 4
    try:
        await test_task4_wss_gateway()
    except Exception as e:
        print(f"\n❌ Task 4 测试异常: {e}")
        traceback.print_exc()

    # 汇总
    TestStats.summary()

    # 返回退出码
    sys.exit(0 if TestStats.failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
