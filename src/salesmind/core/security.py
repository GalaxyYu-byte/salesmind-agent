"""密码格式与 JWT；兼容已有 PBKDF2 样例账号，不返回凭据。"""

import base64
import hashlib
import hmac
import secrets
import time
from uuid import uuid4

import jwt
from fastapi import HTTPException

from salesmind.core.config import Settings

PASSWORD_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        iterations = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), iterations
        )
        return hmac.compare_digest(base64.b64encode(actual).decode(), expected)
    except (ValueError, TypeError):
        return False


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signing_key(settings: Settings) -> str:
    if settings.auth_jwt_secret is None:
        raise HTTPException(503, "登录服务尚未配置签名密钥")
    return settings.auth_jwt_secret.get_secret_value()


def issue_access(
    settings: Settings, user_id: str, session_id: str, expires_at: int
) -> tuple[str, int]:
    now = int(time.time())
    lifetime = min(settings.auth_access_seconds, expires_at - now)
    if lifetime <= 0:
        raise HTTPException(401, "登录已过期，请重新登录")
    token = jwt.encode(
        {
            "sub": user_id,
            "sid": session_id,
            "type": "access",
            "iat": now,
            "exp": now + lifetime,
            "iss": "salesmind",
            "aud": "salesmind-api",
            "jti": str(uuid4()),
        },
        signing_key(settings),
        algorithm="HS256",
    )
    return token, lifetime


def decode_access(settings: Settings, token: str) -> dict:
    key = signing_key(settings)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            issuer="salesmind",
            audience="salesmind-api",
            options={"require": ["sub", "sid", "type", "iat", "exp", "jti"]},
        )
        if (
            claims["type"] != "access"
            or not isinstance(claims["sid"], str)
            or not isinstance(claims["sub"], str)
        ):
            raise jwt.InvalidTokenError()
        return claims
    except jwt.InvalidTokenError:
        raise HTTPException(
            401, "登录凭证无效或已过期", headers={"WWW-Authenticate": "Bearer"}
        ) from None
