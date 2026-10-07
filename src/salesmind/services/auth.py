"""验证码与设备会话存于 Redis；刷新通过 Lua 原子比较并轮换。"""

import base64
import hmac
import io
import json
import secrets
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from redis.asyncio import Redis

from salesmind.core.security import digest_token

PREFIX = "salesmind:auth:"
COOKIE_NAME = "salesmind_refresh"
COOKIE_PATH = "/api/v1/auth"
CAPTCHA_SECONDS = 120

CONSUME = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""
ROTATE = """
local value = redis.call('GET', KEYS[1])
if not value then return 0 end
local record = cjson.decode(value)
if record.refresh_hash ~= ARGV[1] then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return 0 end
record.refresh_hash = ARGV[2]
redis.call('SET', KEYS[1], cjson.encode(record), 'PX', ttl)
return 1
"""
REVOKE = """
local value = redis.call('GET', KEYS[1])
if not value then return 0 end
local record = cjson.decode(value)
if record.refresh_hash ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""
RATE = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def session_key(sid: str) -> str:
    return PREFIX + "session:" + sid


def refresh_parts(value: str | None) -> tuple[str, str]:
    try:
        sid, token = (value or "").split(".", 1)
        if (
            str(UUID(sid)) != sid
            or not 40 <= len(token) <= 100
            or not all(c.isalnum() or c in "-_" for c in token)
        ):
            raise ValueError()
        return sid, digest_token(token)
    except ValueError:
        raise HTTPException(401, "登录已过期，请重新登录") from None


async def rate_limit(
    redis: Redis, identifier: str, action: str, limit: int, seconds: int = 60
) -> None:
    key = PREFIX + f"rate:{action}:" + digest_token(identifier)
    if await redis.eval(RATE, 1, key, seconds) > limit:
        raise HTTPException(
            429, "操作过于频繁，请稍后重试", headers={"Retry-After": str(seconds)}
        )


def captcha_image(answer: str) -> str:
    """PNG 只含像素；响应与 Redis 不存可读取的明文答案。"""
    image = Image.new("RGB", (168, 56), "#f2f5eb")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    for _ in range(6):
        draw.line(
            (
                secrets.randbelow(168),
                secrets.randbelow(56),
                secrets.randbelow(168),
                secrets.randbelow(56),
            ),
            fill="#acb99c",
            width=1,
        )
    for i, char in enumerate(answer):
        draw.text(
            (14 + i * 36, 7 + secrets.randbelow(8)), char, font=font, fill="#365742"
        )
    for _ in range(110):
        draw.point((secrets.randbelow(168), secrets.randbelow(56)), fill="#98aa8d")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


async def new_captcha(redis: Redis) -> dict:
    cid = str(uuid4())
    answer = "".join(
        secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4)
    )
    await redis.set(
        PREFIX + "captcha:" + cid, digest_token(cid + ":" + answer), ex=CAPTCHA_SECONDS
    )
    return {
        "captcha_id": cid,
        "image": captcha_image(answer),
        "expires_in": CAPTCHA_SECONDS,
    }


async def consume_captcha(redis: Redis, cid: str, answer: str) -> None:
    expected = await redis.eval(CONSUME, 1, PREFIX + "captcha:" + cid)
    supplied = digest_token(cid + ":" + answer.upper())
    if expected is None or not hmac.compare_digest(expected, supplied):
        raise HTTPException(400, "验证码错误或已过期，请获取新验证码")


async def new_session(redis: Redis, user, lifetime: int) -> tuple[str, str, dict]:
    sid = str(uuid4())
    token = secrets.token_urlsafe(48)
    record = {
        "user_id": user.id,
        "refresh_hash": digest_token(token),
        "password_stamp": digest_token(user.password_hash),
        "expires_at": int(time.time()) + lifetime,
    }
    await redis.set(session_key(sid), json.dumps(record), ex=lifetime)
    return sid, sid + "." + token, record


async def read_session(redis: Redis, sid: str) -> dict:
    value = await redis.get(session_key(sid))
    if value is None:
        raise HTTPException(401, "当前设备已退出或登录已过期")
    record = json.loads(value)
    if record["expires_at"] <= int(time.time()):
        raise HTTPException(401, "登录已过期，请重新登录")
    return record


async def rotate_session(redis: Redis, sid: str, expected_hash: str) -> str:
    token = secrets.token_urlsafe(48)
    if not await redis.eval(
        ROTATE, 1, session_key(sid), expected_hash, digest_token(token)
    ):
        raise HTTPException(401, "刷新凭证已使用或已失效，请重新登录")
    return sid + "." + token
