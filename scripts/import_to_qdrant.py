"""
P0 知识库导入工具 (V3.0 重构版)

功能:
  1. 从 Excel/CSV/TXT 文件批量导入销售话术
  2. 调用 Embedding API 生成向量 (独立于 LLM 对话模型)
  3. 批量写入 Qdrant 向量数据库

工程要求:
  - Collection 安全检测:  使用 collection_exists() 而非 get_collection()
  - Embedding 接口解耦:   从 settings.embedding_api_base 独立读取
  - 批量 Embedding:      每批 32 条，按 index 排序保证顺序
  - 确定性 UUID:          uuid.uuid5(NAMESPACE_DNS, text) 实现幂等去重
  - 零向量兜底:           Embedding 失败时降级为零向量 + 日志告警

用法:
  python scripts/import_to_qdrant.py data/sales_knowledge.xlsx
  python scripts/import_to_qdrant.py data/faq.csv --collection my_kb
  python scripts/import_to_qdrant.py data/scripts.txt --batch-size 64
"""

import argparse
import asyncio
import hashlib
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings


# ════════════════════════════════════════════════════════════
# 数据加载器 — 兼容 Excel / CSV / TXT
# ════════════════════════════════════════════════════════════

def load_data(file_path: str) -> List[Dict[str, Any]]:
    """
    从文件加载预处理后的知识文本。

    支持格式:
      - .xlsx / .xls: Excel 文件，识别 text / content / 内容 列
      - .csv:         CSV 文件，同上列名识别
      - .txt:         纯文本，按空行分割为独立知识条目

    Returns:
        [{"text": "知识点内容", "metadata": {...}}, ...]
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    records: List[Dict[str, Any]] = []

    if suffix in (".xlsx", ".xls"):
        # ── Excel 加载 ──────────────────────────────────────────
        df = pd.read_excel(file_path)
        text_col = None
        for candidate in ["text", "content", "内容", "knowledge", "知识点", "话术"]:
            if candidate in df.columns:
                text_col = candidate
                break

        if text_col is None:
            # 回退：使用第一列
            text_col = df.columns[0]
            print(f"[DataLoader] ⚠️ 未识别 text/content 列，使用首列: '{text_col}'")

        for _, row in df.iterrows():
            text = str(row[text_col]).strip()
            if not text or text.lower() == "nan":
                continue

            metadata = {}
            for col in df.columns:
                if col != text_col and pd.notna(row[col]):
                    metadata[col] = str(row[col])

            records.append({"text": text, "metadata": metadata})

    elif suffix == ".csv":
        # ── CSV 加载 ────────────────────────────────────────────
        df = pd.read_csv(file_path)
        text_col = None
        for candidate in ["text", "content", "内容", "knowledge", "知识点", "话术"]:
            if candidate in df.columns:
                text_col = candidate
                break

        if text_col is None:
            text_col = df.columns[0]
            print(f"[DataLoader] ⚠️ 未识别 text/content 列，使用首列: '{text_col}'")

        for _, row in df.iterrows():
            text = str(row[text_col]).strip()
            if not text or text.lower() == "nan":
                continue

            metadata = {}
            for col in df.columns:
                if col != text_col and pd.notna(row[col]):
                    metadata[col] = str(row[col])

            records.append({"text": text, "metadata": metadata})

    elif suffix == ".txt":
        # ── 纯文本加载 ──────────────────────────────────────────
        raw_text = file_path.read_text(encoding="utf-8")
        # 按空行分割
        blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]
        for block in blocks:
            records.append({"text": block, "metadata": {"source": file_path.name}})

    else:
        raise ValueError(f"不支持的文件格式: {suffix} (支持: .xlsx / .csv / .txt)")

    print(f"[DataLoader] 从 '{file_path.name}' 加载 {len(records)} 条知识记录")
    return records


# ════════════════════════════════════════════════════════════
# Embedding 服务 — 批量调用 + 零向量兜底
# ════════════════════════════════════════════════════════════

class EmbeddingService:
    """
    Embedding 向量化服务。

    设计要点:
      - 独立 API Base/Key/Model，与 LLM 对话模型完全解耦
      - 批量提交 (默认 32 条/批)，按 index 排序保证顺序
      - 单条失败 → 零向量兜底 + 日志告警，不阻断整体流程
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.http = http_client or httpx.AsyncClient(timeout=60.0)
        self.api_base = settings.embedding_api_base.rstrip("/")
        self.api_key = settings.embedding_api_key
        self.model = settings.embedding_model
        self.batch_size = settings.embedding_batch_size

    # ── 单条 Embedding (用于维度探测) ───────────────────────

    async def get_embedding(self, text: str) -> List[float]:
        """
        对单条文本生成 Embedding 向量。

        Args:
            text: 输入文本

        Returns:
            向量列表（失败时返回零向量）
        """
        results = await self.get_embeddings_batch([text])
        return results[0]

    # ── 批量 Embedding (核心性能优化) ───────────────────────

    async def get_embeddings_batch(
        self, texts: List[str]
    ) -> List[List[float]]:
        """
        批量生成 Embedding 向量。

        实现要点:
          1. 按 batch_size 分批提交
          2. 每批内部按原始 index 排序恢复顺序
          3. 单条失败 → 零向量降级 + 告警
          4. API 鉴权失败 → 直接抛出（无法继续）

        Args:
            texts: 文本列表

        Returns:
            向量列表，与输入 texts 顺序一致
        """
        total = len(texts)
        all_results: Dict[int, List[float]] = {}  # index → vector
        embedding_dim: Optional[int] = None  # 从首次成功响应动态获取

        for batch_start in range(0, total, self.batch_size):
            batch_end = min(batch_start + self.batch_size, total)
            batch_texts = texts[batch_start:batch_end]
            batch_indices = list(range(batch_start, batch_end))

            # ── API 请求 ─────────────────────────────────────────
            url = f"{self.api_base}/embeddings"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "input": batch_texts,
                "encoding_format": "float",
            }

            try:
                response = await self.http.post(url, json=payload, headers=headers)

                if response.status_code == 401 or response.status_code == 403:
                    raise RuntimeError(
                        f"Embedding API 鉴权失败 ({response.status_code})！\n"
                        f"请检查 .env 中 EMBEDDING_API_KEY 和 EMBEDDING_API_BASE 配置。\n"
                        f"当前 API Base: {self.api_base}\n"
                        f"响应: {response.text[:300]}"
                    )

                response.raise_for_status()
                resp_data = response.json()

                # ── 解析响应 ──────────────────────────────────────
                # 兼容 OpenAI / SiliconFlow 格式: data[].embedding
                data_items = resp_data.get("data", [])
                if not data_items:
                    raise RuntimeError(f"Embedding API 返回空 data: {resp_data}")

                # 按 index 排序 (API 返回顺序可能不一致)
                data_items.sort(key=lambda x: x.get("index", 0))

                for i, item in enumerate(data_items):
                    actual_idx = batch_indices[i] if i < len(batch_indices) else batch_start + i
                    vector = item.get("embedding", [])
                    all_results[actual_idx] = vector

                    # 动态获取向量维度
                    if embedding_dim is None and vector:
                        embedding_dim = len(vector)

                print(
                    f"  [Embedding] 进度: {batch_end}/{total} 条完成 "
                    f"(批次 {batch_start//self.batch_size + 1}, "
                    f"维度={embedding_dim or '?'})"
                )

            except httpx.HTTPStatusError as e:
                # HTTP 错误（非 401/403）→ 该批次全部降级为零向量
                print(f"  [Embedding] ⚠️ HTTP {e.response.status_code} 于批次 {batch_start}-{batch_end}: {e}")
                for idx in batch_indices:
                    all_results[idx] = [0.0] * (embedding_dim or 1024)

            except Exception as e:
                # 网络错误/超时 → 该批次降级为零向量
                print(f"  [Embedding] ⚠️ 批次 {batch_start}-{batch_end} 失败: {type(e).__name__}: {e}")
                for idx in batch_indices:
                    all_results[idx] = [0.0] * (embedding_dim or 1024)

        # ── 按 index 排序组装最终结果 ─────────────────────────
        final_vectors: List[List[float]] = []
        for i in range(total):
            vec = all_results.get(i)
            if vec is None:
                # 极端情况：某条记录完全无结果
                vec = [0.0] * (embedding_dim or 1024)
                print(f"  [Embedding] ⚠️ 索引 {i} 无结果，使用零向量")
            final_vectors.append(vec)

        return final_vectors

    async def close(self) -> None:
        await self.http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


# ════════════════════════════════════════════════════════════
# Qdrant 导入引擎
# ════════════════════════════════════════════════════════════

def generate_deterministic_uuid(text: str) -> str:
    """
    基于文本内容生成确定性 UUID (v5)。

    用途: 多次运行脚本时，相同内容的记录不会重复写入，
         不同文件/来源的相同知识点会被自动去重。

    Args:
        text: 知识点文本内容

    Returns:
        UUID v5 字符串
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, text))


async def import_to_qdrant(
    file_path: str,
    collection_name: Optional[str] = None,
    batch_size: Optional[int] = None,
) -> Tuple[int, int]:
    """
    主导入流程。

    Args:
        file_path:      数据文件路径
        collection_name: Qdrant 集合名称 (默认取配置)
        batch_size:      Embedding 批量大小 (默认取配置)

    Returns:
        (成功条数, 总条数)
    """
    collection_name = collection_name or settings.qdrant_collection
    if batch_size:
        settings.embedding_batch_size = batch_size  # 运行时覆盖

    # ── Step 1: 加载数据 ────────────────────────────────────
    records = load_data(file_path)
    if not records:
        print("❌ 未加载到任何有效记录，退出。")
        return (0, 0)

    # ── Step 2: 初始化 Qdrant 客户端 ────────────────────────
    qdrant = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    # ── Step 3: Collection 安全存在性检测 (修复致命 Bug) ────
    # 严禁使用 try: qdrant.get_collection() (会抛 404 异常)
    # 必须使用官方推荐的 collection_exists()
    embedding_dim: int

    if qdrant.collection_exists(collection_name):
        # 已有集合 → 获取现有向量维度
        existing_info = qdrant.get_collection(collection_name)
        embedding_dim = existing_info.config.params.vectors.size
        print(
            f"[Qdrant] 集合 '{collection_name}' 已存在，向量维度={embedding_dim}"
        )
    else:
        # 新建集合 → 先用探测文本获取向量维度
        print(f"[Qdrant] 集合 '{collection_name}' 不存在，正在创建...")

        async with EmbeddingService() as embed_svc:
            probe_vector = await embed_svc.get_embedding("维度测试文本")
            embedding_dim = len(probe_vector)

        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=embedding_dim,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
        print(
            f"[Qdrant] 集合 '{collection_name}' 创建成功，向量维度={embedding_dim}"
        )

    # ── Step 4: 文本批量向量化 ──────────────────────────────
    texts = [r["text"] for r in records]
    print(f"[Embedding] 开始向量化 {len(texts)} 条文本...")

    async with EmbeddingService() as embed_svc:
        vectors = await embed_svc.get_embeddings_batch(texts)

    print(f"[Embedding] 向量化完成: {len(vectors)} 条, 维度={len(vectors[0])}")

    # ── Step 5: 批量写入 Qdrant ─────────────────────────────
    # 修复: 过滤零向量（Embedding 失败时降级产物），避免写入 Qdrant 污染集合
    write_batch_size = 100  # Qdrant 写入批大小
    points_batch: List[qdrant_models.PointStruct] = []
    success_count = 0
    skipped_zero_vector = 0

    for idx, (record, vector) in enumerate(zip(records, vectors)):
        # 零向量检测: 全 0 或接近 0 的向量（Embedding API 失败/超时的兜底产物）
        try:
            is_zero = all(abs(v) < 1e-12 for v in vector) if vector else True
        except Exception:
            is_zero = True
        if is_zero or not vector:
            skipped_zero_vector += 1
            print(
                f"  [Qdrant] ⚠️ 跳过零向量记录 (idx={idx}): "
                f"'{record['text'][:30]}...' — Embedding 失败，不写入集合"
            )
            continue

        # 基于内容生成确定性 UUID（幂等去重）
        point_id = generate_deterministic_uuid(record["text"])

        payload = {
            "text": record["text"],
            "source_file": Path(file_path).name,
        }
        payload.update(record.get("metadata", {}))

        points_batch.append(
            qdrant_models.PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
        )

        # 每 write_batch_size 条或最后一批时写入
        if len(points_batch) >= write_batch_size or idx == len(records) - 1:
            try:
                qdrant.upsert(
                    collection_name=collection_name,
                    points=points_batch,
                )
                success_count += len(points_batch)
                print(
                    f"  [Qdrant] 写入进度: {success_count}/{len(records)} 条 "
                    f"(最近 UUID: {points_batch[-1].id})"
                )
            except Exception as e:
                print(f"  [Qdrant] ⚠️ 批次写入失败: {e}")
            finally:
                points_batch.clear()

    qdrant.close()

    if skipped_zero_vector > 0:
        print(
            f"  [Qdrant] ⚠️ 完成: 成功写入 {success_count} 条，"
            f"跳过零向量 {skipped_zero_vector} 条（Embedding 失败，请检查 Embedding API 配置）"
        )
    else:
        print(f"  [Qdrant] ✅ 完成: 成功写入 {success_count} 条，无零向量跳过")
    return (success_count, len(records))


# ════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(
        description="无界AI超级员工系统 - 知识库批量导入工具 (V3.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python scripts/import_to_qdrant.py data/sales_knowledge.xlsx
  python scripts/import_to_qdrant.py data/faq.csv --collection my_kb
  python scripts/import_to_qdrant.py data/scripts.txt --batch-size 64
        """,
    )
    parser.add_argument("file", help="数据文件路径 (.xlsx / .csv / .txt)")
    parser.add_argument(
        "--collection", "-c",
        default=None,
        help="Qdrant 集合名称 (默认取 .env 中 QDRANT_COLLECTION)",
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=None,
        help="Embedding 批量大小 (默认 32)",
    )

    args = parser.parse_args()

    # 验证文件存在
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)

    print("=" * 60)
    print("🚀 无界AI超级员工系统 - 知识库导入工具 (V3.0)")
    print("=" * 60)
    print(f"  文件:         {file_path}")
    print(f"  集合:         {args.collection or settings.qdrant_collection}")
    print(f"  Embedding API: {settings.embedding_api_base}")
    print(f"  Embedding 模型: {settings.embedding_model}")
    print(f"  批量大小:     {args.batch_size or settings.embedding_batch_size}")
    print(f"  Qdrant:       {settings.qdrant_host}:{settings.qdrant_port}")
    print("=" * 60)
    print()

    # 校验 Embedding API Key
    if not settings.embedding_api_key:
        print("❌ EMBEDDING_API_KEY 未设置！")
        print("   请在 .env 中配置 EMBEDDING_API_KEY 后重试。")
        sys.exit(1)

    success, total = await import_to_qdrant(
        file_path=str(file_path),
        collection_name=args.collection,
        batch_size=args.batch_size,
    )

    print()
    print("=" * 60)
    if success == total:
        print(f"🎉 导入完成: {success}/{total} 条全部写入成功!")
    else:
        print(f"⚠️  导入完成: {success}/{total} 条成功, {total - success} 条失败")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
