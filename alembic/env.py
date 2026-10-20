import asyncio
from logging.config import fileConfig
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
from src.config import settings
from src.models.base import Base
from src.models.property import Property
from src.models.agents import Agents
from src.models.areas import Area
from src.models.faq_categories import FaqCategory
from src.models.faqs import Faq
from src.models.cors_origins import CorsOrigin
from src.models.whatsapp import WhatsappConversation, WhatsappEvent, WhatsappMessage

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Advisory lock de PostgreSQL: si arrancan varias réplicas a la vez, solo una aplica las migraciones
# y las demás esperan a que termine (y luego ven la base de datos ya al día).
MIGRATION_LOCK_ID = 727_100_001

CHECKPOINT_TABLES = { "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations" }

def include_name(name, type_, parent_names):
    if type_ == "table":
        return name not in CHECKPOINT_TABLES
    return True

def run_migrations_offline() -> None:
    url = settings.database_url.render_as_string(hide_password=True)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
    connection.commit()
    try:
        context.configure(connection=connection, target_metadata=target_metadata, include_name=include_name)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
        connection.commit()

async def run_async_migrations() -> None:
    connectable = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
