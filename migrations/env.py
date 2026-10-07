"""MySQL 双 schema 的异步 Alembic 环境。"""

import asyncio
from logging.config import fileConfig

from alembic import context

from salesmind.db.migration import configure_migrations, run_online_migrations

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if context.is_offline_mode():
    configure_migrations(config, url="mysql+asyncmy://", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        configure_migrations(config, connection=supplied_connection)
        with context.begin_transaction():
            context.run_migrations()
    else:
        asyncio.run(run_online_migrations(config))
