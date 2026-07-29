"""
无界AI超级员工系统 - 核心 Schema 规范 (Pydantic V2)

严格使用 Pydantic V2 语法:
- model_config = {"arbitrary_types_allowed": ...}  替代 class Config
- field_validator                                  替代 @validator
- model_dump()                                     替代 dict()
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ════════════════════════════════════════════════════════════
# 1. MongoDB Trace 日志模型
# ════════════════════════════════════════════════════════════

class TraceLogModel(BaseModel):
    """全链路追溯日志，存入 MongoDB trace_logs 集合"""
    trace_id: str = Field(..., description="全链路唯一追溯ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    account_id: str = Field(..., description="操作账号ID")
    customer_id: str = Field(..., description="客户ID")
    user_input: str = Field(..., description="用户原始输入")
    intent_detected: Optional[str] = Field(default=None, description="检测到的意图")
    retrieved_docs: List[Dict[str, Any]] = Field(default_factory=list, description="RAG 检索到的文档")
    prompt_system: Optional[str] = Field(default=None, description="实际使用的 System Prompt")
    llm_raw_output: Optional[str] = Field(default=None, description="LLM 原始输出")
    human_intervention: bool = Field(default=False, description="是否经过人工干预")
    human_edited_output: Optional[str] = Field(default=None, description="人工修改后的输出")
    final_sent_output: Optional[str] = Field(default=None, description="最终发送给用户的输出")
    delay_seconds_applied: float = Field(default=0.0, description="实际应用的延迟秒数")
    status: str = Field(default="PENDING", description="状态: PENDING / CONFIRMED / SENT / CANCELLED")

    model_config = {"arbitrary_types_allowed": True}


# ════════════════════════════════════════════════════════════
# 2. RAG 纠错反馈表模型 (PostgreSQL)
# ════════════════════════════════════════════════════════════

class RAGFeedbackModel(BaseModel):
    """人机协作反馈，存入 PostgreSQL rag_feedback 表"""
    trace_id: str = Field(..., description="关联的 trace_id")
    context_text: str = Field(..., description="检索到的上下文原文")
    ai_raw_output: str = Field(..., description="AI 原始输出")
    human_edited_output: str = Field(..., description="人工修订后的输出")
    status: str = Field(default="PENDING", description="反馈状态: PENDING / APPROVED / REJECTED")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"arbitrary_types_allowed": True}


# ════════════════════════════════════════════════════════════
# 3. API 请求/响应模型
# ════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    """单条对话消息"""
    role: str = Field(..., description="角色: user / assistant")
    content: str = Field(..., description="消息内容")


class ChatGenerateRequest(BaseModel):
    """POST /api/v1/chat/generate 请求体"""
    account_id: str = Field(..., description="操作账号ID")
    customer_id: str = Field(..., description="客户ID")
    user_message: str = Field(..., min_length=1, description="用户消息")
    history: List[ChatMessage] = Field(default_factory=list, description="历史对话记录")


class ChatGenerateResponse(BaseModel):
    """POST /api/v1/chat/generate 响应体"""
    trace_id: str = Field(..., description="全链路追溯ID")
    generated_text: str = Field(..., description="生成的回复文本或拒答词")
    is_fallback: bool = Field(default=False, description="是否为兜底拒答")
    max_score: Optional[float] = Field(default=None, description="最高向量检索匹配得分")
    status: str = Field(default="PENDING", description="状态: PENDING / CONFIRMED / SENT / CANCELLED")


class ConfirmSendRequest(BaseModel):
    """POST /api/v1/chat/confirm_send 请求体"""
    trace_id: str = Field(..., description="追溯ID")
    final_text: str = Field(..., description="最终发送文本")
    is_modified: bool = Field(default=False, description="是否经过人工修改")
    action: str = Field(..., description="操作: ACCEPT / REJECT / MODIFY")


class ConfirmSendResponse(BaseModel):
    """POST /api/v1/chat/confirm_send 响应体"""
    trace_id: str = Field(..., description="追溯ID")
    status: str = Field(default="QUEUED", description="操作结果: QUEUED / REJECTED / ERROR")
    message: str = Field(default="操作成功", description="操作结果说明")
