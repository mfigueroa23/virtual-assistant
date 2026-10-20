import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from src.agents.coordinator import coordinator_builder
from src.config import settings
from src.database.session import engine
from src.middlewares.cors import DatabaseCORSMiddleware
from src.routers.health import router as health_router
from src.routers.chat import route as chat_router
from src.routers.google_chat import router as google_chat_router
from src.routers.whatsapp import router as whatsapp_router
from src.services import whatsapp_reply
from src.services.google_chat_reply import cancel_pending

async def check_connection(conn: AsyncConnection[DictRow]) -> None:
    await conn.execute("SELECT 1")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncConnectionPool(
        settings.checkpointer_url,
        connection_class = AsyncConnection[DictRow],
        kwargs = { "autocommit": True, "prepare_threshold": 0, "row_factory": dict_row },
        check = check_connection,
        min_size = 1,
        max_size = 2,
        open = False
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        app.state.graph = coordinator_builder.compile(checkpointer = checkpointer)
        # Retoma los lotes de WhatsApp que quedaron sin responder en el despliegue anterior.
        await whatsapp_reply.recover(app.state.graph)
        yield
        # Antes de cerrar el pool: las respuestas diferidas en curso lo usan. Se cancelan y queda en el log cuántas.
        await cancel_pending()
        await whatsapp_reply.cancel_pending()
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
app.add_middleware(DatabaseCORSMiddleware)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(google_chat_router)
app.include_router(whatsapp_router)

app_logger = logging.getLogger("src")
app_logger.handlers = logging.getLogger("uvicorn").handlers
app_logger.setLevel(settings.LOG_LEVEL)
app_logger.propagate = False
