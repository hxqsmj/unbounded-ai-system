"""
P1 RAG 向量检索验证脚本 (V3.0)

功能:
  1. 接收测试提问，生成 Embedding 查询向量
  2. 连接 Qdrant 检索 Top-N 匹配项
  3. 打印相似度得分 + 匹配文本
  4. 判断断路逻辑: score >= 阈值 → PASS / < 阈值 → FALLBACK

用法:
  python scripts/verify_qdrant.py
  python scripts/verify_qdrant.py --query "产品A多少钱" --top 3
  python scripts/verify_qdrant.py --query "今天天气" --threshold 0.5
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

import httpx
from qdrant_client import QdrantClient

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings


# ════════════════════════════════════════════════════════════
# 查询 Embedding 生成
# ════════════════════════════════════════════════════════════

async def get_query_embedding(text: str) -> Optional[List[float]]:
    """
    调用 Embedding API 生成查询向量。

    Args:
        text: 查询文本

    Returns:
        向量列表，失败返回 None
    """
    url = f"{settings.embedding_api_base.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.embedding_model,
        "input": [text],
        "encoding_format": "float",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(url, json=payload, headers=headers)

            if response.status_code in (401, 403):
                print(f"❌ Embedding API 鉴权失败 ({response.status_code})")
                print(f"   请检查 .env 中 EMBEDDING_API_KEY 配置。")
                return None

            response.raise_for_status()
            resp_data = response.json()

            data_items = resp_data.get("data", [])
            if not data_items:
                print(f"❌ Embedding API 返回空 data")
                return None

            vector = data_items[0].get("embedding", [])
            print(f"[Embedding] 查询向量生成成功，维度={len(vector)}")
            return vector

    except httpx.HTTPStatusError as e:
        print(f"❌ Embedding API HTTP {e.response.status_code}: {e}")
        return None
    except Exception as e:
        print(f"❌ Embedding API 调用失败: {type(e).__name__}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# Qdrant 检索 + 断路判断
# ════════════════════════════════════════════════════════════

def verify_rag(
    query: str,
    collection_name: str,
    top_k: int = 1,
    threshold: Optional[float] = None,
) -> bool:
    """
    RAG 检索验证主流程。

    Args:
        query:           查询文本
        collection_name: Qdrant 集合名
        top_k:           返回条数
        threshold:       断路阈值 (默认取配置)

    Returns:
        True 表示命中知识库 (score >= threshold)
    """
    threshold = threshold or settings.qdrant_score_threshold

    print("=" * 60)
    print("🔍 RAG 向量检索验证")
    print("=" * 60)
    print(f"  查询:    {query}")
    print(f"  集合:    {collection_name}")
    print(f"  Top-K:   {top_k}")
    print(f"  阈值:    {threshold}")
    print(f"  模型:    {settings.embedding_model}")
    print("=" * 60)
    print()

    # ── Step 1: 生成查询向量 ────────────────────────────────
    query_vector = asyncio.run(get_query_embedding(query))
    if query_vector is None:
        print("❌ 查询向量生成失败，请检查 Embedding API 配置。")
        return False

    # ── Step 2: Qdrant 连接 + Collection 安全检测 ──────────
    qdrant = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    if not qdrant.collection_exists(collection_name):
        print(f"❌ 集合 '{collection_name}' 不存在！")
        print(f"   请先运行: python scripts/import_to_qdrant.py data/sales_knowledge.csv")
        qdrant.close()
        return False

    # 获取集合信息
    info = qdrant.get_collection(collection_name)
    print(f"[Qdrant] 集合信息: 向量数={info.points_count}, 维度={info.config.params.vectors.size}")
    print()

    # ── Step 3: 向量检索 ────────────────────────────────────
    results = qdrant.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        with_payload=True,
    ).points

    # ── Step 4: 打印结果 + 断路判断 ─────────────────────────
    if not results:
        print("⚠️  未检索到任何匹配结果 (集合可能为空)")
        qdrant.close()
        return False

    print(f"📊 Top-{top_k} 检索结果:")
    print("-" * 60)

    top_score = results[0].score if results else 0.0
    hit = top_score >= threshold

    for i, hit_result in enumerate(results):
        score = hit_result.score
        text = hit_result.payload.get("text", "N/A") if hit_result.payload else "N/A"
        marker = "✅" if score >= threshold else "⚠️"

        print(f"  #{i+1} {marker} Score: {score:.4f}")
        print(f"     Text: {text[:80]}...")
        if hit_result.payload:
            meta = {k: v for k, v in hit_result.payload.items() if k != "text"}
            if meta:
                print(f"     Meta: {meta}")
        print()

    # ── Step 5: 断路判断 ────────────────────────────────────
    print("=" * 60)
    if hit:
        print(f"🎉 [PASS] 成功命中知识库！")
        print(f"   最高得分 {top_score:.4f} >= 阈值 {threshold}")
        print(f"   系统将调用 LLM 生成回复。")
    else:
        print(f"🛡️  [TRIGGER_FALLBACK] 触发防幻觉断路拒答")
        print(f"   最高得分 {top_score:.4f} < 阈值 {threshold}")
        print(f"   系统将返回兜底拒答词: 「{settings.fallback_reply}」")
    print("=" * 60)

    qdrant.close()
    return hit


# ════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="无界AI超级员工系统 - RAG 检索验证工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python scripts/verify_qdrant.py
  python scripts/verify_qdrant.py --query "产品A多少钱" --top 3
  python scripts/verify_qdrant.py --query "今天天气" --threshold 0.5
        """,
    )
    parser.add_argument(
        "--query", "-q",
        default="你们的产品价格是多少？",
        help="测试查询文本",
    )
    parser.add_argument(
        "--collection", "-c",
        default=None,
        help="Qdrant 集合名称 (默认取 .env 配置)",
    )
    parser.add_argument(
        "--top", "-k",
        type=int,
        default=3,
        help="返回 Top-K 条结果 (默认 3)",
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=None,
        help="断路阈值 (默认取 .env 配置)",
    )

    args = parser.parse_args()

    # 校验 API Key
    if not settings.embedding_api_key:
        print("❌ EMBEDDING_API_KEY 未设置！请在 .env 中配置后重试。")
        sys.exit(1)

    hit = verify_rag(
        query=args.query,
        collection_name=args.collection or settings.qdrant_collection,
        top_k=args.top,
        threshold=args.threshold,
    )

    sys.exit(0 if hit else 1)


if __name__ == "__main__":
    main()
