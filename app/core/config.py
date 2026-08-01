"""
无界AI超级员工系统 - 配置中心 (V3.0)

所有敏感词、风控阈值、模型参数均从环境变量或 .env 文件读取。
严禁硬编码任何业务参数。
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局配置，自动从 .env / 环境变量加载"""

    # ── 应用基础 ──────────────────────────────────────────
    app_name: str = "无界AI超级员工系统"
    app_version: str = "3.0.0"
    debug: bool = False

    # ── MongoDB (Trace 日志) ──────────────────────────────
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "unbounded_ai"
    mongo_trace_collection: str = "trace_logs"

    # ── PostgreSQL (RAG 反馈) ─────────────────────────────
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_db: str = "unbounded_ai"

    # ── Redis (延迟队列) ──────────────────────────────────
    redis_url: Optional[str] = None  # None = 仅内存模式; "redis://host:port" 启用持久化
    redis_queue_key: str = "typing_delay_queue"

    # ── Qdrant (向量检索) ─────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "knowledge_base"
    qdrant_top_k: int = 3
    qdrant_score_threshold: float = 0.65  # 低于此分直接拒答

    # ── LLM 对话模型配置 ──────────────────────────────────
    llm_api_base: str = "https://api.openai.com/v1"  # OpenAI-compatible 端点 (DeepSeek / DashScope / SiliconFlow)
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # ── Embedding 向量模型配置 (独立于 LLM) ────────────────
    embedding_api_base: str = "https://api.siliconflow.cn/v1"  # 如 SiliconFlow / DashScope
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"  # bge-m3 / text-embedding-3-small 等
    embedding_batch_size: int = 32  # 批量 Embedding 每批条数

    # ── 风控参数 ───────────────────────────────────────────
    sleep_start_hour: int = 23  # 夜间防封开始
    sleep_end_hour: int = 7     # 夜间防封结束
    typing_speed_min: float = 0.2   # 每字最低打字秒数
    typing_speed_max: float = 0.4   # 每字最高打字秒数
    thinking_time_min: float = 3.0  # 最低思考时间
    thinking_time_max: float = 8.0  # 最高思考时间

    # ── 兜底拒答词 ─────────────────────────────────────────
    fallback_reply: str = "已为您记录，稍后将由专人客服为您处理。"

    # ── 系统 Prompt 模板 (V4.0 销冠版) ──────────────────
    system_prompt_template: str = (
        "【身份设定】\n"
        "你是嘉兴晨邦装饰工程有限公司的资深地坪技术顾问，"
        "在环氧地坪、水性聚氨酯砂浆、防静电地坪、固化地坪、硅PU球场、"
        "塑胶跑道、PVC地板等领域有十余年一线施工与销售经验。"
        "客户都叫你晨邦小黄，你为人实诚、干活仔细，专门负责给客户免费勘测、带样品打样，"
        "以及对项目施工全程把控。客户都信任你这个实在人。\n\n"
        "【沟通铁律 - 必须严格遵守】\n"
        "1. 严禁在回复中出现以下词汇或类似表述：\n"
        "   - 禁止: 根据参考信息 / 参考文档中 / 资料显示\n"
        "   - 禁止: 作为人工智能 / 我是AI助手 / 系统检索到\n"
        "   - 禁止: 抱歉我无法 / 我没有相关信息 / 超出我的知识范围\n"
        "   你就是一个真实的地坪老师傅，要说人话，不要说机器话。\n\n"
        "2. 知识库信息 = 你脑子里的经验。"
        "直接用你自己的话把知识讲出来，不要引述任何来源或文档。\n\n"
        "3. 遇到知识库中确实没写到的具体细节（如精确到每平方的报价、某市具体工期）：\n"
        "   你一定不能编造数字，但也绝不能生硬拒答。\n"
        "   你应该这样说：\n"
        "  这个要根据现场实际情况来定——基层平整度、施工厚度、面积大小都会影响最终报价。"
        "咱们嘉兴晨邦提供免费上门勘测打样，"
        "技术员看完现场给你出一个详细的方案和报价清单，这样最精准，你看啥时候方便？"
        "也可以先拨18606859158，我安排时间去现场看看。\n\n"
        "4. 每条回复都要自然地、不着痕迹地带出以下信息中的至少一项：\n"
        "   - 免费上门勘测 / 免费打样\n"
        "   - 包工包料一站式服务\n"
        "   - 官方电话 18606859158\n"
        "   - 质保政策和养护指导\n"
        "   但不要每条都机械地全部堆上去，要根据对话场景有选择地、自然地提。\n\n"
        "5. 回复结构建议（灵活使用，不要生搬硬套）：\n"
        "   先正面回答客户问题（用老销售的方式讲）\n"
        "   补充1-2个关键工程参数或注意事项（展示专业性）\n"
        "   自然引导下一步行动（免费勘测 / 电话详聊 / 案例参观）\n\n"
        "6. 语气尺度：专业但不油腻，热情但不啰嗦。"
        "像是一个经验丰富的大师傅在跟客户喝茶聊天时给出的建议。\n\n"
        "【你的专业知识储备】\n"
        "{context}"
    )

    # ── WSS 网关 ───────────────────────────────────────────
    wss_host: str = "127.0.0.1"
    wss_port: int = 8765
    wss_heartbeat_interval: int = 30    # 心跳间隔（秒）
    wss_max_missed_pongs: int = 3       # 连续丢失 Pong 触发告警
    wss_enabled: bool = True            # 是否随 FastAPI 进程启动 WSS 网关（生产必须开启）
    wss_buffer_max: int = 1000          # Hook 离线时单账号消息缓冲上限（防止无限增长）

    # ── API 服务器 ─────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8001

    # ── API 鉴权 ───────────────────────────────────────────
    # 设置后所有 /api/v1 接口与 WebSocket 连接必须携带该 token
    # ⚠ 为空时系统无鉴权（仅限本地开发），生产环境必须设置！
    api_token: str = ""

    # ── Redis 发送重试 ─────────────────────────────────────
    redis_send_max_retries: int = 3     # 发送失败最大重试次数
    redis_send_retry_delay: int = 60    # 重试间隔（秒）

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    @property
    def pg_dsn(self) -> str:
        """构建 asyncpg 连接字符串"""
        return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_db}"


# 全局单例
settings = Settings()
