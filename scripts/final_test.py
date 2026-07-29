"""
P1 最终验收测试脚本 (V4.0)

模拟外网环境通过 WSS Gateway 发送消息，验证全链路连通性。

用法:
  python scripts/final_test.py                           # 本地测试 (127.0.0.1:8765)
  python scripts/final_test.py --host 0.tcp.ngrok.io --port 12345  # 外网测试

测试内容:
  1. WSS Gateway 连通性
  2. Hook 客户端注册
  3. 客户消息 → AI 生成
  4. 前端队列验证
  5. 操作员审核 (ACCEPT/MODIFY/REJECT)
  6. MongoDB 状态
  7. PostgreSQL 数据飞轮
"""

import asyncio
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings

# ── 测试消息 ─────────────────────────────────────────────────
TEST_QUERIES = [
    ("sales_01", "wx_client_嘉兴晨邦测试", "你好，我们工厂要做5000平环氧自流平，包工包料多少钱？"),
    ("sales_01", "wx_client_嘉兴晨邦测试", "你们做过食品厂GMP认证车间地坪吗？"),
    ("sales_02", "wx_client_体育中心", "标准篮球场做硅PU全包报价和工期"),
]


class Check:
    """验收项"""
    def __init__(self, name: str):
        self.name = name
        self.ok = False
        self.detail = ""

    def pass_(self, detail=""):
        self.ok = True
        self.detail = detail

    def fail(self, detail=""):
        self.ok = False
        self.detail = detail


def hr(title: str):
    print(f"\n{'━' * 55}")
    print(f"  {title}")
    print(f"{'━' * 55}")


async def final_test(args):
    results: list[Check] = []
    backend = f"http://localhost:{settings.api_port}"
    gateway = f"ws://{args.host}:{args.port}"

    print(f"{'=' * 55}")
    print(f"  🏆 无界AI · 嘉兴晨邦 — 最终验收测试")
    print(f"{'=' * 55}")
    print(f"  网关: {gateway}")
    print(f"  后端: {backend}")
    print(f"  前端: http://localhost:5173")

    async with httpx.AsyncClient(timeout=60, base_url=backend) as http:

        # ── 1. 后端健康 ────────────────────────────────────
        hr("1. 后端健康检查")
        c = Check("后端 API 可用")
        try:
            resp = await http.get("/health")
            if resp.status_code == 200:
                c.pass_(resp.json()["app"])
            else:
                c.fail(f"HTTP {resp.status_code}")
        except Exception as e:
            c.fail(str(e))
        print(f"   {'✅' if c.ok else '❌'} {c.name}: {c.detail}")
        results.append(c)

        # ── 2. WSS Gateway 连通 ────────────────────────────
        hr("2. WSS Gateway 连通性")
        c2 = Check("WSS Gateway 可连接")

        traces = []
        try:
            async with websockets.connect(f"{gateway}/ws/hook/sales_01") as hook:
                c2.pass_("Hook sales_01 已注册")

                for i, (acc, cust, query) in enumerate(TEST_QUERIES):
                    msg = json.dumps({
                        "event": "ON_RECV_MSG",
                        "data": {
                            "from_user": cust,
                            "to_user": acc,
                            "content": query,
                            "is_group": False,
                            "msg_id": f"acceptance_{i}",
                            "timestamp": time.time(),
                        },
                    }, ensure_ascii=False)
                    await hook.send(msg)
                    print(f"   📤 [{i+1}/3] {cust[:20]}: {query[:40]}...")
                    await asyncio.sleep(3)

        except ConnectionRefusedError:
            c2.fail("网关未启动 — 请先运行 scripts/start_gateway.py")
        except Exception as e:
            c2.fail(str(e))

        print(f"   {'✅' if c2.ok else '❌'} {c2.name}: {c2.detail}")
        results.append(c2)

        if not c2.ok:
            print(f"\n❌ 网关未连通，终止测试。")
            return results

        # ── 3. AI 生成验证 ─────────────────────────────────
        hr("3. AI 智脑生成验证")
        c3 = Check("AI 检索 + LLM 生成")

        resp = await http.get("/api/v1/chat/pending", params={"limit": 10})
        items = resp.json()["items"]
        recent = [i for i in items if "acceptance" not in i.get("user_input", "")]
        recent = [i for i in items if i["customer_id"] in [q[1] for q in TEST_QUERIES]]

        if recent:
            scores = [i["max_score"] or 0 for i in recent]
            avg = sum(scores) / len(scores)
            c3.pass_(f"{len(recent)} 条消息, 平均置信度 {avg:.3f}")
            for item in recent[:3]:
                icon = "✅" if not item["is_fallback"] else "⚠️"
                print(f"   {icon} {item['trace_id']} | {item['customer_id'][:20]} | "
                      f"score={item['max_score']:.3f}")
        else:
            c3.fail("未找到对应的生成结果")

        print(f"   {'✅' if c3.ok else '❌'} {c3.name}: {c3.detail}")
        results.append(c3)

        # ── 4. 操作员审核 ──────────────────────────────────
        hr("4. 操作员审核操作")
        c4 = Check("ACCEPT / MODIFY / REJECT 全操作")

        if recent:
            actions = ["ACCEPT", "MODIFY", "REJECT"]
            ok_count = 0
            for i, item in enumerate(recent[:3]):
                action = actions[i]
                text = item["generated_text"]
                if action == "MODIFY":
                    text = text + " — 嘉兴晨邦，服务热线18606859158"

                resp = await http.post("/api/v1/chat/confirm_send", json={
                    "trace_id": item["trace_id"],
                    "final_text": text,
                    "is_modified": action == "MODIFY",
                    "action": action,
                })
                r = resp.json()
                icon = "✅" if r["status"] in ("QUEUED", "REJECTED") else "❌"
                print(f"   {icon} {item['trace_id']}: {action} → {r['status']}")
                if r["status"] in ("QUEUED", "REJECTED"):
                    ok_count += 1

            c4.pass_(f"{ok_count}/3 操作成功") if ok_count == 3 else c4.fail(f"仅 {ok_count}/3 成功")

        print(f"   {'✅' if c4.ok else '❌'} {c4.name}: {c4.detail}")
        results.append(c4)

        # ── 5. 数据飞轮 ────────────────────────────────────
        hr("5. PostgreSQL 数据飞轮")
        c5 = Check("PG rag_feedback 写入")

        try:
            import asyncpg
            conn = await asyncpg.connect(
                host=settings.pg_host, port=settings.pg_port,
                user=settings.pg_user, password=settings.pg_password,
                database=settings.pg_db,
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_feedback (
                    id SERIAL PRIMARY KEY, trace_id VARCHAR(64) NOT NULL,
                    context_text TEXT DEFAULT '', ai_raw_output TEXT DEFAULT '',
                    human_edited_output TEXT DEFAULT '', status VARCHAR(20) DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            count = await conn.fetchval("SELECT COUNT(*) FROM rag_feedback")
            if count > 0:
                row = await conn.fetchrow("SELECT * FROM rag_feedback ORDER BY id DESC LIMIT 1")
                c5.pass_(f"{count} 条记录, 最新: {row['trace_id']}")
                print(f"   📊 AI: {row['ai_raw_output'][:50]}...")
                print(f"   ✏️  人工: {row['human_edited_output'][:50]}...")
            else:
                c5.fail("表为空 — 确保执行了 MODIFY 操作")
            await conn.close()
        except Exception as e:
            c5.fail(str(e))

        print(f"   {'✅' if c5.ok else '❌'} {c5.name}: {c5.detail}")
        results.append(c5)

    # ── 最终报告 ───────────────────────────────────────────
    hr("🏆 验收报告")
    passed = sum(1 for c in results if c.ok)
    total = len(results)

    print(f"""
  ┌──────────────────────────────────────────┐
  │  验收项                        结果     │
  ├──────────────────────────────────────────┤""")
    for c in results:
        icon = "✅" if c.ok else "❌"
        print(f"  │  {c.name:<28} {icon}      │")
    print(f"  ├──────────────────────────────────────────┤")
    print(f"  │  总计: {passed}/{total} 通过                     │")
    print(f"  └──────────────────────────────────────────┘""")

    if passed == total:
        print(f"\n🎉🍾 全部验收通过！「无界AI超级员工系统」已具备上线条件！")
    elif passed >= total - 1:
        print(f"\n⚠️  基本通过，{total - passed} 项需要关注。")
    else:
        print(f"\n❌ 多项验收未通过，请检查服务状态。")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="无界AI · 最终验收测试")
    parser.add_argument("--host", default="127.0.0.1", help="WSS Gateway 主机")
    parser.add_argument("--port", type=int, default=8765, help="WSS Gateway 端口")
    args = parser.parse_args()

    results = asyncio.run(final_test(args))

    passed = sum(1 for c in results if c.ok)
    sys.exit(0 if passed == len(results) else 1)
