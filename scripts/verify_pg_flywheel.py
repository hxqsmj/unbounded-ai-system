"""
P1 PostgreSQL 数据飞轮验证脚本 (V3.0)

验证操作员点击"修改后发送 (MODIFY)"后，纠错数据是否精准落盘到 rag_feedback 表。

用法:
  python scripts/verify_pg_flywheel.py

前提:
  - 已运行 e2e_full_chain.py 完成至少一次 MODIFY 操作
  - PostgreSQL 已启动并可连接
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings


GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BOLD = '\033[1m'
END = '\033[0m'


async def verify():
    print(f"{CYAN}{'=' * 60}{END}")
    print(f"{BOLD}🔍 PostgreSQL 数据飞轮验证{END}")
    print(f"{CYAN}{'=' * 60}{END}")
    print(f"  Host: {settings.pg_host}:{settings.pg_port}")
    print(f"  DB:   {settings.pg_db}")
    print(f"  User: {settings.pg_user}")
    print()

    try:
        conn = await asyncpg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_db,
        )
        print(f"{GREEN}✅ PostgreSQL 连接成功{END}\n")

        # ── 确保表存在 ──────────────────────────────────────
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
        print("   rag_feedback 表就绪")

        # ── 查询所有记录 ────────────────────────────────────
        total = await conn.fetchval("SELECT COUNT(*) FROM rag_feedback")
        print(f"   总记录数: {total}\n")

        if total == 0:
            print(f"{YELLOW}⚠️  rag_feedback 表为空。{END}")
            print(f"   请先运行端到端测试触发 MODIFY 操作:")
            print(f"   {CYAN}python scripts/e2e_full_chain.py{END}")
            await conn.close()
            return False

        # ── 逐条展示最新记录 ────────────────────────────────
        rows = await conn.fetch("""
            SELECT trace_id, context_text, ai_raw_output, human_edited_output, status, created_at
            FROM rag_feedback
            ORDER BY id DESC
            LIMIT 5
        """)

        print(f"{BOLD}📊 最新 {len(rows)} 条反馈记录:{END}")
        for i, row in enumerate(rows):
            print(f"""
  ┌─────────────────────────────────────────────┐
  │ {BOLD}记录 #{total - i}{END}  trace_id: {row['trace_id']}
  ├─────────────────────────────────────────────┤
  │ AI 原始回复:  {row['ai_raw_output'][:55]}...
  │ 人工修改后:   {row['human_edited_output'][:55]}...
  │ 状态:         {row['status']}
  │ 创建时间:     {row['created_at']}
  └─────────────────────────────────────────────┘""")

        # ── 断言检查 ────────────────────────────────────────
        latest = rows[0]
        checks_passed = 0
        checks_total = 4

        # 1. trace_id 非空
        if latest['trace_id']:
            checks_passed += 1
            print(f"   ✅ trace_id 有效: {latest['trace_id']}")
        else:
            print(f"   ❌ trace_id 为空")

        # 2. AI 原始回复存在
        if latest['ai_raw_output']:
            checks_passed += 1
            print(f"   ✅ AI 原始回复已记录")
        else:
            print(f"   ❌ AI 原始回复为空")

        # 3. 人工修改回复存在（且与原始不同）
        if latest['human_edited_output'] and latest['human_edited_output'] != latest['ai_raw_output']:
            checks_passed += 1
            print(f"   ✅ 人工修改回复已记录（与 AI 原始不同）")
        else:
            print(f"   ❌ 人工修改回复异常")

        # 4. 状态为 PENDING
        if latest['status'] == 'PENDING':
            checks_passed += 1
            print(f"   ✅ 状态正确: PENDING")
        else:
            print(f"   ❌ 状态异常: {latest['status']}")

        await conn.close()

        # ── 最终判定 ────────────────────────────────────────
        print()
        if checks_passed == checks_total:
            print(f"{GREEN}{BOLD}╔══════════════════════════════════════════╗{END}")
            print(f"{GREEN}{BOLD}║  🎉 [PASS] 恭喜！                       ║{END}")
            print(f"{GREEN}{BOLD}║  PostgreSQL 数据飞轮反馈回路已完美闭环！ ║{END}")
            print(f"{GREEN}{BOLD}╚══════════════════════════════════════════╝{END}")
            print(f"\n   ✅ {checks_passed}/{checks_total} 项检查通过")
            print(f"   📈 数据飞轮正在为微调模型积累宝贵的人机纠错数据")
            return True
        else:
            print(f"{RED}⚠️  {checks_passed}/{checks_total} 项检查通过，{checks_total - checks_passed} 项失败{END}")
            return False

    except Exception as e:
        print(f"{RED}❌ PostgreSQL 连接或查询失败:{END}")
        print(f"   {type(e).__name__}: {e}")
        print(f"\n   请检查:")
        print(f"   1. PostgreSQL 是否启动 (docker ps | grep postgres)")
        print(f"   2. .env 中 PG_HOST/PG_PORT/PG_USER/PG_PASSWORD 是否正确")
        print(f"   3. 数据库 unbounded_ai 是否已创建")
        return False


if __name__ == "__main__":
    ok = asyncio.run(verify())
    sys.exit(0 if ok else 1)
