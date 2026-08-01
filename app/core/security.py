"""
API 鉴权 — 最简单的 Token 校验 (V4.1)

规则:
  - settings.api_token 为空 → 放行（仅限本地开发，启动时打印警告）
  - settings.api_token 非空 → 请求必须携带 `Authorization: Bearer <token>`
    或 `X-API-Token: <token>`，否则 401

校验范围:
  - 所有 /api/v1/chat/* 路由（router dependencies）
  - FastAPI /ws 前端面板连接（query 参数 ?token=）
  - WSS Gateway /ws/hook/{account_id} Hook 连接（query 参数 ?token=）

/health 与 / 端点放行（部署脚本与健康检查需要）。
"""

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def _token_matches(provided: str) -> bool:
    """恒定时间比较，防时序攻击"""
    if not settings.api_token:
        return True  # 未配置 token 时放行（仅限开发）
    if not provided:
        return False
    # 容忍首尾空白 (手机输入法/粘贴易带空格)
    provided = provided.strip()
    return hmac.compare_digest(provided.encode(), settings.api_token.encode())


def verify_api_token(
    authorization: str = Header(default=""),
    x_api_token: str = Header(default="", alias="X-API-Token"),
) -> None:
    """FastAPI 依赖：校验请求头中的 token"""
    provided = ""
    if authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()
    if not provided:
        provided = x_api_token.strip()

    if not _token_matches(provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权的请求：缺少或无效的 API Token",
        )


def verify_ws_token(token: str) -> bool:
    """WebSocket 连接鉴权（query 参数形式）"""
    return _token_matches(token)


def auth_enabled() -> bool:
    """是否启用了鉴权（health 端点报告用）"""
    return bool(settings.api_token)
