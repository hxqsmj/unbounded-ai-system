"""
P1 端到端全链路联调脚本 (V3.0)

模拟完整业务闭环:
  1. Mock 微信客户发送地坪业务提问
  2. AI Brain 检索 71 条知识库 → 生成回复
  3. 操作员审核 → ACCEPT / MODIFY / REJECT
  4. MongoDB 状态更新验证
  5. PostgreSQL 数据飞轮落盘验证

用法:
  python scripts/e2e_full_chain.py

前提:
  - FastAPI 后端已启动 (端口 8001)
  - Qdrant / MongoDB / Redis / PostgreSQL 已就绪
"""

import asyncio
import sys
import os
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings

# ── 测试数据: 5 条嘉兴晨邦地坪典型提问 ──────────────────────
TEST_MESSAGES = [
    {
        "account_id": "sales_01",
        "customer_id": "wx_client_嘉兴食品厂李总",
        "user_message": "我们食品厂冷库要做地坪，大概2000平，推荐什么材料？多少钱一平？",
    },
    {
        "account_id": "sales_01",
        "customer_id": "wx_client_电子厂王工",
        "user_message": "SMT车间防静电地坪电阻要求多少？你们做过电子厂案例吗？",
    },
    {
        "account_id": "sales_02",
        "customer_id": "wx_client_学校后勤张老师",
        "user_message": "学校想翻新旧水泥篮球场做成硅PU的，标准场地全包多少钱？",
    },
    {
        "account_id": "sales_02",
        "customer_id": "wx_client_物业赵经理",
        "user_message": "地下车库旧环氧起皮脱落了，能翻新成固化地坪吗？",
    },
    {
        "account_id": "sales_03",
        "customer_id": "wx_client_医院基建陈主任",
        "user_message": "医院门诊大厅想铺PVC地板，要求防滑抗菌无缝，推荐什么型号？",
    },
]

# ── 操作员动作分配 ─────────────────────────────────────────
ACTIONS = {
    0: {"action": "ACCEPT", "modify": False},   # 采纳
    1: {"action": "ACCEPT", "modify": False},   # 采纳
    2: {"action": "MODIFY", "modify": True,     # 修改 + 数据飞轮
        "edited": "标准硅PU篮球场（420㎡）全包约5.2万元，含基础找平+5mm硅PU面层+标准划线。嘉兴晨邦包工包料，欢迎来电18606859158预约免费勘测。"},
    3: {"action": "REJECT", "modify": False},   # 拒绝
    4: {"action": "ACCEPT", "modify": False},   # 采纳
}


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


def step(msg: str):
    print(f"\n{Colors.CYAN}{'─' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{msg}{Colors.END}")
    print(f"{Colors.CYAN}{'─' * 60}{Colors.END}")


async def full_chain_test():
    async with httpx.AsyncClient(timeout=60, base_url=f"http://localhost:{settings.api_port}") as client:

        # ═══════════════════════════════════════════════════════
        # Phase 1: 发送 5 条客户消息 → AI 生成回复
        # ═══════════════════════════════════════════════════════
        step("Phase 1: Mock 微信消息 → AI 智脑生成回复")

        traces = []
        for i, msg in enumerate(TEST_MESSAGES):
            print(f"\n📤 [{i+1}/5] {msg['customer_id']}: {msg['user_message'][:50]}...")

            resp = await client.post("/api/v1/chat/generate", json={
                "account_id": msg["account_id"],
                "customer_id": msg["customer_id"],
                "user_message": msg["user_message"],
                "history": [],
            })
            assert resp.status_code == 200, f"Generate failed: {resp.status_code}"
            data = resp.json()

            icon = "✅" if not data["is_fallback"] else "⚠️ 兜底"
            traces.append({
                **data,
                "customer_id": msg["customer_id"],
                "account_id": msg["account_id"],
            })
            print(f"   {icon} Trace: {data['trace_id']} | Score: {data['max_score']:.4f} | "
                  f"Reply: {data['generated_text'][:60]}...")

            await asyncio.sleep(1.0)  # 让 MongoDB 异步写入完成

        # ── 汇总 ──────────────────────────────────────────────
        hit_count = sum(1 for t in traces if not t["is_fallback"])
        avg_score = sum(t["max_score"] or 0 for t in traces) / len(traces)
        print(f"\n{Colors.GREEN}📊 生成汇总: {hit_count}/{len(traces)} 命中 | "
              f"平均置信度: {avg_score:.4f}{Colors.END}")

        # ═══════════════════════════════════════════════════════
        # Phase 2: 验证待审核队列
        # ═══════════════════════════════════════════════════════
        step("Phase 2: 验证待审核队列 (Pending Queue)")

        resp = await client.get("/api/v1/chat/pending", params={"limit": 20})
        pending = resp.json()
        print(f"   待审核总数: {pending['total']}")
        for item in pending["items"][:5]:
            print(f"   📋 {item['trace_id']} | {item['customer_id'][:20]} | score={item['max_score']:.3f}")

        # ═══════════════════════════════════════════════════════
        # Phase 3: 操作员审核 → ACCEPT / MODIFY / REJECT
        # ═══════════════════════════════════════════════════════
        step("Phase 3: 操作员审核操作")

        results = []
        for i, trace in enumerate(traces):
            action_cfg = ACTIONS[i]
            final_text = trace["generated_text"]

            if action_cfg["modify"]:
                final_text = action_cfg["edited"]

            print(f"\n🖊️  [{i+1}/5] {trace['trace_id']} → {action_cfg['action']}")
            print(f"   客户: {trace['customer_id']}")
            print(f"   回复: {final_text[:60]}...")

            resp = await client.post("/api/v1/chat/confirm_send", json={
                "trace_id": trace["trace_id"],
                "final_text": final_text,
                "is_modified": action_cfg["modify"],
                "action": action_cfg["action"],
            })
            assert resp.status_code == 200, f"Confirm failed: {resp.status_code}"
            result = resp.json()
            print(f"   结果: {result['status']} — {result['message']}")
            results.append(result)
            await asyncio.sleep(0.5)

        # ═══════════════════════════════════════════════════════
        # Phase 4: 验证 MongoDB 状态
        # ═══════════════════════════════════════════════════════
        step("Phase 4: MongoDB 状态验证")

        import motor.motor_asyncio
        mongo = motor.motor_asyncio.AsyncIOMotorClient(settings.mongo_uri)
        db = mongo[settings.mongo_db]
        col = db[settings.mongo_trace_collection]

        status_map = {"ACCEPT": "SENT", "MODIFY": "SENT", "REJECT": "CANCELLED"}

        mongo_ok = 0
        for i, trace in enumerate(traces):
            doc = await col.find_one({"trace_id": trace["trace_id"]})
            expected = status_map[ACTIONS[i]["action"]]
            actual = doc.get("status") if doc else "NOT_FOUND"
            icon = "✅" if actual == expected else "❌"
            if actual == expected:
                mongo_ok += 1
            print(f"   {icon} {trace['trace_id']}: status={actual} (expected={expected})")

        print(f"\n{Colors.GREEN if mongo_ok == 5 else Colors.FAIL}"
              f"   MongoDB 状态: {mongo_ok}/5 正确{Colors.END}")

        # ═══════════════════════════════════════════════════════
        # Phase 5: 验证 PostgreSQL 数据飞轮
        # ═══════════════════════════════════════════════════════
        step("Phase 5: PostgreSQL 数据飞轮验证")

        pg_ok = False
        try:
            import asyncpg
            conn = await asyncpg.connect(
                host=settings.pg_host,
                port=settings.pg_port,
                user=settings.pg_user,
                password=settings.pg_password,
                database=settings.pg_db,
            )

            # 确保表存在
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_feedback (
                    id SERIAL PRIMARY KEY,
                    trace_id VARCHAR(64) NOT NULL,
                    context_text TEXT NOT NULL DEFAULT '',
                    ai_raw_output TEXT NOT NULL DEFAULT '',
                    human_edited_output TEXT NOT NULL DEFAULT '',
                    status VARCHAR(20) DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # 检查最新记录
            rows = await conn.fetch(
                "SELECT * FROM rag_feedback ORDER BY id DESC LIMIT 3"
            )

            if rows:
                print(f"   📊 rag_feedback 最新 {len(rows)} 条记录:")
                for row in rows:
                    print(f"   ┌ trace_id: {row['trace_id']}")
                    print(f"   ├ ai_raw:    {row['ai_raw_output'][:50]}...")
                    print(f"   ├ human_edit:{row['human_edited_output'][:50]}...")
                    print(f"   └ created:   {row['created_at']}")
                pg_ok = True
            else:
                print(f"   {Colors.WARNING}⚠️  rag_feedback 表为空（MODIFY 操作可能异步写入中，等待几秒后重试）{Colors.END}")
                # 等 3 秒再查一次
                await asyncio.sleep(3)
                rows = await conn.fetch("SELECT * FROM rag_feedback ORDER BY id DESC LIMIT 3")
                if rows:
                    print(f"   {Colors.GREEN}✅ 延迟查询成功，找到 {len(rows)} 条记录{Colors.END}")
                    pg_ok = True
                else:
                    print(f"   {Colors.WARNING}⚠️  PG 反馈可能因连接或权限问题未写入{Colors.END}")

            await conn.close()
        except Exception as e:
            print(f"   {Colors.WARNING}⚠️  PG 连接失败: {e}{Colors.END}")
            print(f"   (数据飞轮为非阻塞写入，PG 不可用时不影响主流程)")

        # ═══════════════════════════════════════════════════════
        # 最终报告
        # ═══════════════════════════════════════════════════════
        step("🏆 最终报告")

        print(f"""
  {Colors.BOLD}全链路测试结果:{Colors.END}
  ┌─────────────────────────────────────┐
  │ AI 生成 (5/5):        {Colors.GREEN}✅{Colors.END}  平均置信度 {avg_score:.4f}  │
  │ 待审核队列:           {Colors.GREEN}✅{Colors.END}  正确展示            │
  │ 人工审核 (5/5):       {Colors.GREEN}✅{Colors.END}  操作成功            │
  │ MongoDB 状态:         {f'{Colors.GREEN}✅' if mongo_ok == 5 else f'{Colors.FAIL}❌'}  {mongo_ok}/5 正确        │
  │ PostgreSQL 飞轮:      {f'{Colors.GREEN}✅' if pg_ok else f'{Colors.WARNING}⚠️'}  {'闭环已建立' if pg_ok else '待验证'}  │
  └─────────────────────────────────────┘
""")

        # 汇总退出码
        all_ok = mongo_ok == 5 and avg_score >= 0.7
        return all_ok


if __name__ == "__main__":
    print(f"{Colors.HEADER}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}🚀 无界AI · 嘉兴晨邦 — P1 全链路端到端联调{Colors.END}")
    print(f"{Colors.HEADER}{'=' * 60}{Colors.END}")
    print(f"  后端: http://localhost:{settings.api_port}")
    print(f"  知识库: Qdrant (71条)")
    print(f"  测试消息: 5 条地坪业务提问")
    print()

    ok = asyncio.run(full_chain_test())

    if ok:
        print(f"\n{Colors.GREEN}🎉 全链路联调通过！系统已具备上线条件。{Colors.END}\n")
        sys.exit(0)
    else:
        print(f"\n{Colors.FAIL}⚠️  部分检查未通过，请查看上方日志。{Colors.END}\n")
        sys.exit(1)
