"""从环境变量和项目根目录的 .env 加载配置。"""

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.connection import parse_url
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    """运行账号分别连接业务库与应用库，密码不出现在配置表示中。"""

    model_config = SettingsConfigDict(
        env_prefix="SALESMIND_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
        frozen=True,
        populate_by_name=True,
    )

    app_name: str = "SalesMind Agent"
    auth_jwt_secret: SecretStr | None = None
    auth_access_seconds: int = Field(default=1800, ge=60, le=3600)
    auth_refresh_seconds: int = Field(default=604800, ge=1800, le=604800)
    auth_cookie_secure: bool = True
    auth_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )
    sales_query_timeout: float = Field(default=10, gt=0, le=60)
    sales_cache_seconds: int = Field(default=60, ge=0, le=3600)
    sales_cache_max_bytes: int = Field(default=5000000, ge=1024, le=20000000)
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("SALESMIND_LLM_API_KEY", "DEEPSEEK_API_KEY"),
    )
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = Field(default="deepseek-flash", min_length=1, max_length=100)
    llm_timeout: float = Field(default=30, gt=0, le=60)
    run_concurrency: int = Field(default=4, ge=1, le=20)
    run_queue_limit: int = Field(default=32, ge=1, le=1000)
    run_timeout: float = Field(default=90, gt=0, le=300)
    run_shutdown_seconds: float = Field(default=10, gt=0, le=60)
    artifact_retention_days: int = Field(default=7, ge=1, le=30)
    sse_poll_seconds: float = Field(default=0.5, ge=0.05, le=5)
    sse_heartbeat_seconds: float = Field(default=15, ge=1, le=60)
    sales_database_url: SecretStr | None = None
    app_database_url: SecretStr | None = None
    allow_shared_database_account: bool = False
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout: float = Field(default=30, gt=0)
    db_pool_recycle: int = Field(default=1800, ge=1)
    db_connect_timeout: int = Field(default=10, ge=1)
    redis_url: SecretStr | None = None
    redis_max_connections: int = Field(default=20, ge=1, le=1000)
    redis_connect_timeout: float = Field(default=3, gt=0)
    redis_socket_timeout: float = Field(default=3, gt=0)
    health_check_timeout: float = Field(default=5, gt=0)

    @model_validator(mode="after")
    def validate_llm_url(self) -> "Settings":
        url = urlsplit(self.llm_base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("模型地址必须为不含凭据、查询参数的 HTTP/HTTPS URL")
        return self

    @model_validator(mode="after")
    def validate_auth_secret(self) -> "Settings":
        if self.auth_jwt_secret is not None:
            secret = self.auth_jwt_secret.get_secret_value()
            if len(secret.encode()) < 32 or secret == "REPLACE_ME":
                raise ValueError(
                    "JWT 签名密钥至少需要 32 字节，请使用随机生成的独立密钥"
                )
        if self.auth_refresh_seconds < self.auth_access_seconds:
            raise ValueError("Refresh Token 有效期不能短于 Access Token")
        return self

    @property
    def database_enabled(self) -> bool:
        """两项 DSN 均已配置时启用数据库。"""
        return self.sales_database_url is not None and self.app_database_url is not None

    @model_validator(mode="after")
    def validate_database_urls(self) -> "Settings":
        """提前拒绝驱动、schema 和账号混用，错误中不包含 DSN。"""
        if (self.sales_database_url is None) != (self.app_database_url is None):
            raise ValueError("业务库和应用库 DSN 必须同时配置，或同时留空")
        if not self.database_enabled:
            return self

        usernames = []
        for schema, secret in (
            ("sales_data", self.sales_database_url),
            ("agent_app", self.app_database_url),
        ):
            try:
                url = make_url(secret.get_secret_value())
            except (ArgumentError, ValueError):
                raise ValueError(f"{schema} DSN 格式无效") from None
            if url.drivername != "mysql+asyncmy":
                raise ValueError(f"{schema} DSN 必须使用 mysql+asyncmy 驱动")
            if not url.host or not url.username or not url.password:
                raise ValueError(f"{schema} DSN 必须包含主机、账号和密码")
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError(f"{schema} DSN 端口必须在 1 到 65535 之间")
            if url.database != schema:
                raise ValueError(f"{schema} DSN 必须连接 {schema} schema")
            usernames.append(url.username)
        if usernames[0] == usernames[1] and not self.allow_shared_database_account:
            raise ValueError("业务库只读账号与应用库读写账号必须使用不同用户名")
        return self

    @model_validator(mode="after")
    def validate_redis_url(self) -> "Settings":
        """Redis URL 只支持 TCP/TLS 与非负数据库编号，错误隐藏凭据。"""
        if self.redis_url is None:
            return self
        try:
            url = urlsplit(self.redis_url.get_secret_value())
            port = url.port
            redis_options = parse_url(self.redis_url.get_secret_value())
            valid = (
                url.scheme in {"redis", "rediss"}
                and bool(url.hostname)
                and (port is None or 1 <= port <= 65535)
                and (
                    url.path in {"", "/"}
                    or url.path[1:].isascii()
                    and url.path[1:].isdigit()
                )
                and not url.fragment
                and redis_options.get("db", 0) >= 0
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "Redis URL 必须使用 redis/rediss 协议、有效主机端口和非负数据库编号"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """启动时读取一次配置；修改环境变量后重启服务。"""
    return Settings()
