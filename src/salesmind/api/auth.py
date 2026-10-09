"""登录、刷新和退出；Cookie 仅传 Refresh Token，JSON 仅传 Access Token。"""

import hmac
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from salesmind.api.dependencies import AppSession, RedisClient
from salesmind.core.security import (
    decode_access,
    digest_token,
    hash_password,
    issue_access,
    signing_key,
    verify_password,
)
from salesmind.db.entities import AppUser
from salesmind.services.auth import (
    COOKIE_NAME,
    COOKIE_PATH,
    REVOKE,
    consume_captcha,
    new_captcha,
    new_session,
    rate_limit,
    read_session,
    refresh_parts,
    rotate_session,
    session_key,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
# 无账号时也执行同等级密码校验，避免简单的耗时差异枚举账号。
DUMMY_HASH = hash_password("salesmind-dummy-password")


class LoginRequest(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: SecretStr = Field(min_length=1, max_length=128)
    captcha_id: UUID
    captcha_code: str = Field(min_length=4, max_length=4, pattern=r"^[A-Za-z0-9]{4}$")


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    allowed_metrics: list[str]
    permission_version: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


def public_user(user: AppUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        allowed_metrics=user.allowed_metrics_json,
        permission_version=user.permission_version,
    )


def csrf_guard(request: Request) -> None:
    """Cookie 写接口要求自定义请求头，并检查配置中的前端来源。"""
    origin = request.headers.get("origin")
    if (
        request.headers.get("x-salesmind-csrf") != "1"
        or (
            origin is not None
            and origin not in request.app.state.settings.auth_allowed_origins
        )
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise HTTPException(403, "请求来源无效，请从登录页面操作")


def set_refresh_cookie(
    response: Response, request: Request, token: str, expires_at: int
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max(0, expires_at - int(time.time())),
        httponly=True,
        secure=request.app.state.settings.auth_cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


async def validate_session_user(
    session: AppSession, redis: RedisClient, sid: str, record: dict
) -> AppUser:
    user = await session.get(AppUser, record["user_id"])
    if (
        user is None
        or not user.active
        or not hmac.compare_digest(
            record["password_stamp"], digest_token(user.password_hash)
        )
    ):
        await redis.delete(session_key(sid))
        raise HTTPException(401, "账号已停用或密码已变更，请重新登录")
    return user


def require_claims(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    if credentials is None:
        raise HTTPException(401, "请先登录", headers={"WWW-Authenticate": "Bearer"})
    return decode_access(request.app.state.settings, credentials.credentials)


async def get_current_user(
    claims: Annotated[dict, Depends(require_claims)],
    session: AppSession,
    redis: RedisClient,
) -> AppUser:
    record = await read_session(redis, claims["sid"])
    if record["user_id"] != claims["sub"]:
        raise HTTPException(401, "登录凭证无效")
    return await validate_session_user(session, redis, claims["sid"], record)


CurrentUser = Annotated[AppUser, Depends(get_current_user)]


@router.get("/captcha")
async def captcha(request: Request, redis: RedisClient):
    signing_key(request.app.state.settings)
    await rate_limit(
        redis, request.client.host if request.client else "unknown", "captcha", 30
    )
    return await new_captcha(redis)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(csrf_guard)])
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AppSession,
    redis: RedisClient,
):
    settings = request.app.state.settings
    signing_key(settings)
    await rate_limit(
        redis, request.client.host if request.client else "unknown", "login-ip", 30, 300
    )
    await rate_limit(redis, body.username.lower(), "login-user", 10, 300)
    await consume_captcha(redis, str(body.captcha_id), body.captcha_code)
    user = await session.scalar(
        select(AppUser).where(func.lower(AppUser.username) == body.username.lower())
    )
    valid = await run_in_threadpool(
        verify_password,
        body.password.get_secret_value(),
        user.password_hash if user else DUMMY_HASH,
    )
    if not valid or user is None or not user.active:
        raise HTTPException(401, "用户名或密码错误，或账号不可用")
    sid, refresh_token, record = await new_session(
        redis, user, settings.auth_refresh_seconds
    )
    # 重复登录同一浏览器时只撤销它已有的会话，不影响其他设备。
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        try:
            previous_sid, previous_hash = refresh_parts(previous)
            await redis.eval(REVOKE, 1, session_key(previous_sid), previous_hash)
        except HTTPException:
            pass
    token, lifetime = issue_access(settings, user.id, sid, record["expires_at"])
    set_refresh_cookie(response, request, refresh_token, record["expires_at"])
    return TokenResponse(
        access_token=token, expires_in=lifetime, user=public_user(user)
    )


@router.post(
    "/refresh", response_model=TokenResponse, dependencies=[Depends(csrf_guard)]
)
async def refresh(
    request: Request, response: Response, session: AppSession, redis: RedisClient
):
    settings = request.app.state.settings
    signing_key(settings)
    sid, expected_hash = refresh_parts(request.cookies.get(COOKIE_NAME))
    record = await read_session(redis, sid)
    if not hmac.compare_digest(record["refresh_hash"], expected_hash):
        raise HTTPException(401, "刷新凭证已使用或已失效")
    user = await validate_session_user(session, redis, sid, record)
    refresh_token = await rotate_session(redis, sid, expected_hash)
    token, lifetime = issue_access(settings, user.id, sid, record["expires_at"])
    set_refresh_cookie(response, request, refresh_token, record["expires_at"])
    return TokenResponse(
        access_token=token, expires_in=lifetime, user=public_user(user)
    )


@router.post("/logout", status_code=204, dependencies=[Depends(csrf_guard)])
async def logout(request: Request, response: Response, redis: RedisClient):
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        try:
            sid, expected_hash = refresh_parts(previous)
            await redis.eval(REVOKE, 1, session_key(sid), expected_hash)
        except HTTPException:
            pass
    response.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        secure=request.app.state.settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser):
    return public_user(user)
