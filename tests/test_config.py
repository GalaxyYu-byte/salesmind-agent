from pathlib import Path

import pytest
from pydantic import ValidationError

from salesmind.core.config import Settings, get_settings

SALES_URL = "mysql+asyncmy://sales_reader:secret%40value@localhost:3306/sales_data"
APP_URL = "mysql+asyncmy://agent_writer:other_secret@localhost:3306/agent_app"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    import os

    for name in os.environ:
        if name.upper().startswith("SALESMIND_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_missing_database_config_allows_basic_startup():
    assert not Settings(_env_file=None).database_enabled


def test_dotenv_and_environment_precedence(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"SALESMIND_APP_NAME=Dotenv\n"
        f"SALESMIND_SALES_DATABASE_URL={SALES_URL}\n"
        f"SALESMIND_APP_DATABASE_URL={APP_URL}\n"
        "UNRELATED_SETTING=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SALESMIND_APP_NAME", "Environment")
    settings = Settings(_env_file=dotenv)
    assert settings.app_name == "Environment"
    assert settings.database_enabled
    assert settings.sales_database_url.get_secret_value() == SALES_URL
    assert "secret%40value" not in repr(settings)
    assert "other_secret" not in settings.model_dump_json()


def test_example_is_runnable_without_database():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert not Settings(_env_file=example).database_enabled


@pytest.mark.parametrize(
    ("sales_url", "app_url"),
    [
        (SALES_URL, None),
        (None, APP_URL),
        ("not-a-url-containing-secret", APP_URL),
        (SALES_URL.replace("mysql+asyncmy", "mysql+pymysql"), APP_URL),
        (SALES_URL.replace("sales_data", "agent_app"), APP_URL),
        (SALES_URL, APP_URL.replace("agent_app", "sales_data")),
        (SALES_URL.replace("sales_reader", "agent_writer"), APP_URL),
        (SALES_URL.replace("secret%40value", ""), APP_URL),
        (SALES_URL.replace("localhost", ""), APP_URL),
        (SALES_URL.replace(":3306", ":not-a-port"), APP_URL),
        (SALES_URL.replace(":3306", ":65536"), APP_URL),
    ],
)
def test_invalid_database_config_is_rejected_without_exposing_secrets(
    sales_url, app_url
):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, sales_database_url=sales_url, app_database_url=app_url)
    assert "secret%40value" not in str(error.value)
    assert "other_secret" not in str(error.value)
    assert "not-a-url-containing-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
        ("db_pool_timeout", 0),
        ("db_pool_recycle", 0),
        ("db_connect_timeout", 0),
    ],
)
def test_invalid_pool_limits_are_rejected(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_cached_settings_are_loaded_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SALESMIND_APP_NAME", "First")
    first = get_settings()
    monkeypatch.setenv("SALESMIND_APP_NAME", "Second")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().app_name == "Second"


def test_shared_root_account_requires_explicit_override():
    settings = Settings(
        _env_file=None,
        sales_database_url=SALES_URL.replace("sales_reader", "root"),
        app_database_url=APP_URL.replace("agent_writer", "root"),
        allow_shared_database_account=True,
    )
    assert settings.database_enabled


@pytest.mark.parametrize(
    "url",
    [
        "http://:hidden-secret@localhost/0",
        "redis://:hidden-secret@localhost/-1",
        "redis://:hidden-secret@localhost/invalid",
        "redis://:hidden-secret@localhost:65536/0",
        "redis://:hidden-secret@localhost:bad/0",
        "redis://:hidden-secret@/0",
        "redis://:hidden-secret@localhost/0?db=-1",
        "redis://:hidden-secret@localhost/0?db=invalid",
    ],
)
def test_invalid_redis_urls_hide_passwords(url):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, redis_url=url)
    assert "hidden-secret" not in str(error.value)


def test_redis_configuration_hides_credentials():
    settings = Settings(
        _env_file=None, redis_url="redis://:hidden-secret@localhost:6380/0"
    )
    assert "hidden-secret" not in repr(settings)
    assert "hidden-secret" not in settings.model_dump_json()
