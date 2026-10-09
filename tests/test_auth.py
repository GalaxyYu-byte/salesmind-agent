"""真实 SQLite 用户查询和 Redis Lua 执行，覆盖身份与会话边界。"""

import asyncio
import json
import time
from uuid import uuid4

import fakeredis.aioredis
import httpx
import jwt
import pytest
import pytest_asyncio
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from salesmind.api.dependencies import (
    get_app_session,
    get_redis_client,
    get_sales_session,
)
from salesmind.core.config import Settings
from salesmind.core.security import (
    decode_access,
    digest_token,
    hash_password,
    verify_password,
)
from salesmind.main import create_app
from salesmind.services.auth import COOKIE_NAME, PREFIX, session_key

PASSWORD = "TestPassword-2026!"
PASSWORD_HASH = hash_password(PASSWORD)
USER_ID = str(uuid4())
SECRET = "unit-test-only-signing-key-at-least-32-bytes"
HEADERS = {"X-SalesMind-CSRF": "1", "Origin": "http://127.0.0.1:5173"}


@pytest_asyncio.fixture
async def environment(monkeypatch):
    # 只固定测试图片内容，响应仍不暴露答案；生产代码始终随机生成。
    monkeypatch.setattr("salesmind.services.auth.secrets.choice", lambda options: "A")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("ATTACH DATABASE ':memory:' AS agent_app"))
        await conn.execute(
            text(
                "CREATE TABLE agent_app.app_user (id TEXT PRIMARY KEY, username TEXT, display_name TEXT, "
                "password_hash TEXT, role TEXT, rep_id INTEGER, allowed_metrics_json JSON, "
                "permission_version INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            )
        )
        for username, active in [("tester", 1), ("disabled", 0), ("rep", 1)]:
            await conn.execute(
                text(
                    "INSERT INTO agent_app.app_user VALUES (:id,:name,:display,:password,:role,NULL,'[]',1,:active,NULL,NULL)"
                ),
                {
                    "id": USER_ID if username == "tester" else str(uuid4()),
                    "name": username,
                    "display": "测试用户",
                    "password": PASSWORD_HASH,
                    "role": "SALES_REP" if username == "rep" else "SALES_DIRECTOR",
                    "active": active,
                },
            )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = Settings(
        _env_file=None,
        sales_database_url=None,
        app_database_url=None,
        redis_url=None,
        auth_jwt_secret=SECRET,
        auth_cookie_secure=False,
    )
    app = create_app(settings)

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_app_session] = session_override
    app.dependency_overrides[get_redis_client] = lambda: redis
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers=HEADERS,
        ) as client,
    ):
        yield app, client, redis, settings, engine
    await redis.aclose()
    await engine.dispose()


async def login(client, username="tester", password=PASSWORD, code="aaaa", cid=None):
    if cid is None:
        response = await client.get("/api/v1/auth/captcha")
        assert response.status_code == 200
        cid = response.json()["captcha_id"]
    return await client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_id": cid,
            "captcha_code": code,
        },
    )


def authorization(response):
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.asyncio
async def test_login_cookie_claims_and_redis_hash(environment):
    _, client, redis, settings, _ = environment
    response = await login(client, username="TESTER")
    assert response.status_code == 200
    assert response.json()["expires_in"] == 1800
    assert "refresh_token" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie
    max_age = int(cookie.split("Max-Age=")[1].split(";")[0])
    assert 604790 <= max_age <= 604800
    claims = decode_access(settings, response.json()["access_token"])
    assert claims["exp"] - claims["iat"] == 1800
    value = await redis.get(session_key(claims["sid"]))
    raw_token = client.cookies.get(COOKIE_NAME).split(".", 1)[1]
    assert raw_token not in value
    assert json.loads(value)["refresh_hash"] == digest_token(raw_token)
    me = await client.get("/api/v1/auth/me", headers=authorization(response))
    assert me.status_code == 200 and me.json()["username"] == "tester"
    assert "password" not in me.text


@pytest.mark.asyncio
async def test_captcha_png_single_use_expiry_and_wrong_attempt(environment):
    _, client, redis, _, _ = environment
    captcha = (await client.get("/api/v1/auth/captcha")).json()
    assert captcha["image"].startswith("data:image/png;base64,iVBOR")
    assert set(captcha) == {"image", "captcha_id", "expires_in"}
    cid = captcha["captcha_id"]
    assert await redis.ttl(PREFIX + "captcha:" + cid) <= 120
    assert (await login(client, cid=cid, code="BBBB")).status_code == 400
    assert (await login(client, cid=cid)).status_code == 400
    cid = (await client.get("/api/v1/auth/captcha")).json()["captcha_id"]
    await redis.delete(PREFIX + "captcha:" + cid)
    assert (await login(client, cid=cid)).status_code == 400
    cid = (await client.get("/api/v1/auth/captcha")).json()["captcha_id"]
    assert (await login(client, cid=cid)).status_code == 200
    assert (await login(client, cid=cid)).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "username,password",
    [("tester", "incorrect"), ("unknown", PASSWORD), ("disabled", PASSWORD)],
)
async def test_bad_credentials_are_uniform(environment, username, password):
    _, client, _, _, _ = environment
    response = await login(client, username, password)
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误，或账号不可用"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_rotation_keeps_absolute_expiry_and_rejects_replay(environment):
    _, client, redis, settings, _ = environment
    response = await login(client)
    sid = decode_access(settings, response.json()["access_token"])["sid"]
    original_cookie = client.cookies.get(COOKIE_NAME)
    original_expiry = json.loads(await redis.get(session_key(sid)))["expires_at"]
    await redis.expire(session_key(sid), 100)
    rotated = await client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    assert client.cookies.get(COOKIE_NAME) != original_cookie
    assert await redis.ttl(session_key(sid)) <= 100
    assert (
        json.loads(await redis.get(session_key(sid)))["expires_at"] == original_expiry
    )
    async with httpx.AsyncClient(
        transport=client._transport, base_url="http://localhost", headers=HEADERS
    ) as replay:
        replay.cookies.set(COOKIE_NAME, original_cookie)
        assert (await replay.post("/api/v1/auth/refresh")).status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers=authorization(rotated))
    ).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_refresh_has_one_atomic_winner(environment):
    _, client, _, _, _ = environment
    await login(client)
    cookie = client.cookies.get(COOKIE_NAME)

    async def refresh_one():
        async with httpx.AsyncClient(
            transport=client._transport, base_url="http://localhost", headers=HEADERS
        ) as other:
            other.cookies.set(COOKIE_NAME, cookie)
            return (await other.post("/api/v1/auth/refresh")).status_code

    assert sorted(await asyncio.gather(refresh_one(), refresh_one())) == [200, 401]


@pytest.mark.asyncio
async def test_logout_revokes_only_current_device_and_is_idempotent(environment):
    _, client, _, _, _ = environment
    first = await login(client)
    async with httpx.AsyncClient(
        transport=client._transport, base_url="http://localhost", headers=HEADERS
    ) as device2:
        second = await login(device2)
        assert (await client.post("/api/v1/auth/logout")).status_code == 204
        assert client.cookies.get(COOKIE_NAME) is None
        assert (
            await client.get("/api/v1/auth/me", headers=authorization(first))
        ).status_code == 401
        assert (await client.post("/api/v1/auth/refresh")).status_code == 401
        assert (await client.post("/api/v1/auth/logout")).status_code == 204
        assert (
            await device2.get("/api/v1/auth/me", headers=authorization(second))
        ).status_code == 200
        assert (await device2.post("/api/v1/auth/refresh")).status_code == 200


@pytest.mark.asyncio
async def test_expired_access_can_refresh_but_expired_session_cannot(environment):
    _, client, redis, settings, _ = environment
    response = await login(client)
    claims = decode_access(settings, response.json()["access_token"])
    claims.update(iat=int(time.time()) - 4000, exp=int(time.time()) - 2200)
    expired = jwt.encode(claims, SECRET, algorithm="HS256")
    assert (
        await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer " + expired}
        )
    ).status_code == 401
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    await redis.delete(session_key(claims["sid"]))
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["active=0", "password_hash='changed'"])
async def test_account_disable_and_password_change_revoke_credentials(
    environment, change
):
    _, client, _, _, engine = environment
    response = await login(client)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE agent_app.app_user SET " + change + " WHERE username='tester'")
        )
    assert (
        await client.get("/api/v1/auth/me", headers=authorization(response))
    ).status_code == 401
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_csrf_foreign_origin_and_missing_custom_header_rejected(environment):
    _, client, _, _, _ = environment
    await login(client)
    assert (
        await client.post(
            "/api/v1/auth/refresh", headers={"Origin": "https://evil.example"}
        )
    ).status_code == 403
    assert (
        await client.post("/api/v1/auth/logout", headers={"X-SalesMind-CSRF": ""})
    ).status_code == 403
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_and_failure_response_hide_secrets(environment, monkeypatch):
    _, client, redis, _, _ = environment
    for _ in range(10):
        assert (await login(client, password="wrong")).status_code == 401
    response = await login(client)
    assert response.status_code == 429 and "retry-after" in response.headers

    async def broken(*args, **kwargs):
        raise RedisConnectionError("redis://password@private-host")

    monkeypatch.setattr(redis, "eval", broken)
    response = await client.get("/api/v1/auth/captcha")
    assert response.status_code == 503
    assert "password" not in response.text and "private-host" not in response.text


@pytest.mark.asyncio
async def test_invalid_jwt_and_unauthenticated_sales_rejected_before_query(environment):
    app, client, _, _, _ = environment
    called = False

    async def forbidden_query():
        nonlocal called
        called = True
        yield None

    app.dependency_overrides[get_sales_session] = forbidden_query
    assert (await client.get("/api/v1/sales/overview")).status_code == 401
    assert (
        await client.get(
            "/api/v1/sales/overview", headers={"Authorization": "Bearer forged"}
        )
    ).status_code == 401
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["tester", "rep"])
async def test_active_accounts_can_login_but_ungranted_sales_are_denied(
    environment, username
):
    app, client, _, _, _ = environment

    async def session_override():
        # 实际查询若跳过授权校验，会在使用此 Session 时失败。
        yield None

    app.dependency_overrides[get_sales_session] = session_override
    response = await login(client, username=username)
    assert response.status_code == 200
    assert (
        await client.get("/api/v1/auth/me", headers=authorization(response))
    ).status_code == 200
    denied = await client.get("/api/v1/sales/overview", headers=authorization(response))
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_validation_does_not_echo_password_and_production_cookie_secure(
    environment,
):
    _, client, _, settings, _ = environment
    invalid = await client.post(
        "/api/v1/auth/login",
        json={
            "username": "tester",
            "password": "PRIVATE_VALUE",
            "captcha_id": "invalid",
            "captcha_code": "AAAA",
        },
    )
    assert invalid.status_code == 422 and "PRIVATE_VALUE" not in invalid.text
    assert Settings(_env_file=None).auth_cookie_secure
    with pytest.raises(ValueError):
        Settings(_env_file=None, auth_jwt_secret="short")
    assert (
        settings.auth_access_seconds == 1800 and settings.auth_refresh_seconds == 604800
    )


def test_password_format_is_random_salted_and_handles_bad_hashes():
    assert verify_password(PASSWORD, PASSWORD_HASH)
    assert not verify_password("wrong", PASSWORD_HASH)
    assert hash_password(PASSWORD) != PASSWORD_HASH
    assert not verify_password(PASSWORD, "invalid")
    assert not verify_password(PASSWORD, "pbkdf2_sha256$999999999$salt$hash")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["signature", "algorithm", "issuer", "audience", "type"]
)
async def test_jwt_claims_and_algorithm_are_not_trusted(environment, variant):
    _, client, _, settings, _ = environment
    response = await login(client)
    claims = decode_access(settings, response.json()["access_token"])
    key, algorithm = SECRET, "HS256"
    if variant == "signature":
        key = "another-key-at-least-thirty-two-bytes"
    elif variant == "algorithm":
        algorithm = "HS512"
    elif variant == "issuer":
        claims["iss"] = "other-service"
    elif variant == "audience":
        claims["aud"] = "other-api"
    else:
        claims["type"] = "refresh"
    token = jwt.encode(claims, key, algorithm=algorithm)
    result = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer " + token}
    )
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_secure_cookie_in_https_configuration(environment):
    app, client, _, settings, _ = environment
    app.state.settings = settings.model_copy(update={"auth_cookie_secure": True})
    response = await login(client)
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
