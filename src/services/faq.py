from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.areas import Area
from src.models.faq_categories import FaqCategory
from src.models.faqs import Faq
from src.services.property import get_property
from src.utils.exceptions.database import DatabaseUnavailableError

def to_embedding_text(category: str, question: str, answer: str) -> str:
  # Mismo formato para la reindexación y para las FAQs creadas desde Google Chat: quedan comparables (D15 del plan 009).
  return f"Categoría: {category}\nPregunta: {question}\nRespuesta: {answer}"

@dataclass
class FaqResult:
    category: str
    question: str
    answer: str
    distance: float

async def search_faqs(
  session: AsyncSession,
  vector: list[float],
  area: str, audiencia: str,
) -> list[FaqResult]:
  try:
    distance = Faq.embedding.cosine_distance(vector)
    k = int(await get_property(session, 'top_k'))
    max_distance = float(await get_property(session, 'max_distance'))
    result = await session.execute(
      select(FaqCategory.name, Faq.question, Faq.answer, distance.label("distance"))
      .join(FaqCategory, FaqCategory.id == Faq.category_id)
      .join(Area, Area.id == FaqCategory.area_id)
      .where(
        Area.name == area,
        Area.audiencia == audiencia,
        Faq.embedding.is_not(None),
        distance < max_distance,
      )
      .order_by(distance)
      .limit(k)
    )
    return [FaqResult(*row) for row in result.all()]
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
