import asyncio
import logging
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from sqlalchemy import select, update
from pydantic import SecretStr
from src.database.session import SessionLocal, engine
from src.models.faq_categories import FaqCategory
from src.models.faqs import EMBEDDING_DIMENSIONS, Faq
from src.services.faq import to_embedding_text
from src.services.property import get_property

BATCH_SIZE = 50
logger = logging.getLogger(__name__)

async def reindex() -> None:
    async with SessionLocal() as session:
        embeddings = GoogleGenerativeAIEmbeddings(
            model=await get_property(session, "google_embedding_model"),
            api_key=SecretStr(await get_property(session, "google_api_key")),
            output_dimensionality=EMBEDDING_DIMENSIONS,
        )

        rows = (await session.execute(
            select(Faq.id, Faq.question, Faq.answer, FaqCategory.name)
            .join(FaqCategory)
            .where(Faq.status == "re-index")
            .order_by(Faq.id)
        )).all()
        logger.info(f"FAQ pendientes de indexar {len(rows)}")

        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            vectors = await embeddings.aembed_documents(
                [to_embedding_text(row.name, row.question, row.answer) for row in batch]
            )
            for row, vector in zip(batch, vectors, strict=True):
                await session.execute(
                    update(Faq)
                    .where(Faq.id == row.id, Faq.question == row.question, Faq.answer == row.answer)
                    .values(embedding=vector, status="index")
                )
            await session.commit()
            logger.info(f"Indexadas {start + len(batch)}/{len(rows)}")

    await engine.dispose()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(reindex())
