from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.areas import Area
from src.models.faq_categories import FaqCategory
from src.models.faqs import Faq
from src.models.knowledge import KnowledgeProposal
from src.services.property import get_property
from src.utils.exceptions.database import DatabaseUnavailableError

# El conocimiento que se administra es el de los clientes y contactos: las áreas externo (RF-01).
AUDIENCIA = "externo"
# Área interno del sub-agente que administra el conocimiento (D3 del plan 009).
CONOCIMIENTO = "Conocimiento"

Action = Literal["create", "update", "delete", "category"]
Origin = Literal["interno", "consulta"]
Decision = Literal["apply", "discard"]
# applied/discarded: se resolvió con este clic; duplicate/stale: no se aplicó; resolved: ya estaba resuelta (RF-16 a RF-18).
Outcome = Literal["applied", "discarded", "duplicate", "stale", "resolved"]

@dataclass(frozen = True)
class CategorySummary:
  name: str
  faqs: int

@dataclass(frozen = True)
class AreaSummary:
  name: str
  description: str
  categories: list[CategorySummary]

@dataclass(frozen = True)
class KnowledgeHit:
  id: int
  area: str
  category: str
  question: str
  answer: str
  distance: float

@dataclass(frozen = True)
class Proposal:
  id: str
  origin: str
  action: str
  area: str
  category: str
  faq_id: int | None
  question: str | None
  answer: str | None
  previous_question: str | None
  previous_answer: str | None
  status: str

@dataclass(frozen = True)
class Resolution:
  proposal: Proposal
  outcome: Outcome

def as_proposal(row: KnowledgeProposal, area: str) -> Proposal:
  return Proposal(
    row.id, row.origin, row.action, area, row.category, row.faq_id, row.question, row.answer,
    row.previous_question, row.previous_answer, row.status
  )

async def list_knowledge(session: AsyncSession) -> list[AreaSummary]:
  """Áreas externo con sus categorías y cuántas FAQs tiene cada una (RF-05)."""
  try:
    rows = (await session.execute(
      select(Area.name, Area.description, FaqCategory.name, func.count(Faq.id))
      .outerjoin(FaqCategory, FaqCategory.area_id == Area.id)
      .outerjoin(Faq, Faq.category_id == FaqCategory.id)
      .where(Area.audiencia == AUDIENCIA)
      .group_by(Area.id, Area.name, Area.description, FaqCategory.name)
      .order_by(Area.name, FaqCategory.name)
    )).tuples().all()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  areas: dict[str, AreaSummary] = {}
  for area, description, category, faqs in rows:
    summary = areas.setdefault(area, AreaSummary(area, description, []))
    if category is not None:
      summary.categories.append(CategorySummary(category, faqs))
  return list(areas.values())

async def search_knowledge(session: AsyncSession, vector: list[float]) -> list[KnowledgeHit]:
  """Como search_faqs, pero en todas las áreas externo y con el id de cada FAQ para editarla o borrarla (RF-06)."""
  try:
    distance = Faq.embedding.cosine_distance(vector)
    k = int(await get_property(session, "top_k"))
    max_distance = float(await get_property(session, "max_distance"))
    rows = (await session.execute(
      select(Faq.id, Area.name, FaqCategory.name, Faq.question, Faq.answer, distance.label("distance"))
      .join(FaqCategory, FaqCategory.id == Faq.category_id)
      .join(Area, Area.id == FaqCategory.area_id)
      .where(Area.audiencia == AUDIENCIA, Faq.embedding.is_not(None), distance < max_distance)
      .order_by(distance)
      .limit(k)
    )).tuples().all()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return [KnowledgeHit(*row) for row in rows]

async def external_areas(session: AsyncSession) -> list[str]:
  return list((await session.scalars(select(Area.name).where(Area.audiencia == AUDIENCIA).order_by(Area.name))).all())

async def create_proposal(
  session: AsyncSession,
  origin: Origin,
  action: Action,
  area: str = "",
  category: str = "",
  question: str = "",
  answer: str = "",
  faq_id: int | None = None,
  turn_id: str | None = None
) -> Proposal | str:
  """Guarda un cambio pendiente de confirmar; si no valida devuelve el motivo, sin guardar nada (RF-07 a RF-10)."""
  question, answer, category = question.strip(), answer.strip(), category.strip()
  try:
    previous: tuple[str, str] | None = None
    if action in ("update", "delete"):
      # La FAQ manda el área y la categoría: editar no la cambia de categoría.
      row = (await session.execute(
        select(Area.id, Area.name, FaqCategory.name, Faq.question, Faq.answer)
        .join(FaqCategory, FaqCategory.id == Faq.category_id).join(Area, Area.id == FaqCategory.area_id)
        .where(Faq.id == faq_id, Area.audiencia == AUDIENCIA)
      )).tuples().one_or_none()
      if row is None:
        return f"No existe una pregunta frecuente con id {faq_id}. Búscala primero con buscar_conocimiento."
      area_id, area, category, *current = row
      previous = (current[0], current[1])
      if action == "update":
        question, answer = question or previous[0], answer or previous[1]
        if (question, answer) == previous:
          return "La pregunta y la respuesta quedarían iguales: indica qué cambia."
      else:
        question, answer = "", ""
    else:
      area_id = await session.scalar(select(Area.id).where(Area.name == area.strip(), Area.audiencia == AUDIENCIA))
      if area_id is None:
        return f"El área «{area}» no existe. Áreas disponibles: {', '.join(await external_areas(session))}."
      if not category:
        return "Falta la categoría."
      if action == "create" and not (question and answer):
        return "Faltan la pregunta o la respuesta."
    proposal = KnowledgeProposal(
      id = str(uuid4()), turn_id = turn_id, origin = origin, action = action, area_id = area_id, category = category,
      faq_id = faq_id if previous else None, question = question or None, answer = answer or None,
      previous_question = previous[0] if previous else None, previous_answer = previous[1] if previous else None,
      status = "pending"
    )
    session.add(proposal)
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return as_proposal(proposal, area)

async def get_proposal(session: AsyncSession, proposal_id: str) -> Proposal | None:
  try:
    row = (await session.execute(
      select(KnowledgeProposal, Area.name).join(Area, Area.id == KnowledgeProposal.area_id)
      .where(KnowledgeProposal.id == proposal_id)
    )).tuples().one_or_none()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return as_proposal(*row) if row else None

async def proposals_of_turn(session: AsyncSession, turn_id: str) -> list[Proposal]:
  """Propuestas del turno del «Agente Interno», en orden, para armar sus tarjetas (D2)."""
  try:
    rows = (await session.execute(
      select(KnowledgeProposal, Area.name).join(Area, Area.id == KnowledgeProposal.area_id)
      .where(KnowledgeProposal.turn_id == turn_id).order_by(KnowledgeProposal.created_at)
    )).tuples().all()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return [as_proposal(*row) for row in rows]

async def category_id(session: AsyncSession, area_id: int, name: str) -> int | None:
  return await session.scalar(select(FaqCategory.id).where(FaqCategory.area_id == area_id, FaqCategory.name == name))

async def apply(session: AsyncSession, row: KnowledgeProposal, embedding: list[float] | None) -> Outcome:
  if row.action == "category":
    if await category_id(session, row.area_id, row.category) is not None:
      return "duplicate"
    session.add(FaqCategory(area_id = row.area_id, name = row.category))
    return "applied"
  if row.action == "create":
    category = await category_id(session, row.area_id, row.category)
    if category is None:
      created = FaqCategory(area_id = row.area_id, name = row.category)
      session.add(created)
      await session.flush()
      category = created.id
    elif await session.scalar(select(Faq.id).where(Faq.category_id == category, Faq.question == row.question)):
      return "duplicate"
    session.add(Faq(category_id = category, question = row.question, answer = row.answer, embedding = embedding, status = "index"))
    return "applied"
  if row.faq_id is None:
    return "stale"
  # Solo si la FAQ sigue como se mostró en la tarjeta (D8).
  unchanged = (Faq.id == row.faq_id, Faq.question == row.previous_question, Faq.answer == row.previous_answer)
  if row.action == "update":
    category = await session.scalar(select(Faq.category_id).where(Faq.id == row.faq_id))
    if row.question != row.previous_question and await session.scalar(
      select(Faq.id).where(Faq.category_id == category, Faq.question == row.question, Faq.id != row.faq_id)
    ):
      return "duplicate"
    statement: Any = update(Faq).where(*unchanged).values(
      question = row.question, answer = row.answer, embedding = embedding, status = "index"
    )
  else:
    statement = delete(Faq).where(*unchanged)
  result = cast(CursorResult[Any], await session.execute(statement))
  return "applied" if result.rowcount else "stale"

async def resolve_proposal(
  session: AsyncSession, proposal_id: str, decision: Decision, embedding: list[float] | None = None
) -> Resolution | None:
  """Aplica o descarta una propuesta pendiente en una sola transacción; None si no existe (D9)."""
  try:
    # El bloqueo hace que un segundo clic espere y vea el estado final (RF-18).
    row = await session.get(KnowledgeProposal, proposal_id, with_for_update = True, populate_existing = True)
    if row is None:
      await session.rollback()
      return None
    area = await session.scalar(select(Area.name).where(Area.id == row.area_id)) or ""
    if row.status != "pending":
      # Antes del rollback, que expira la fila y obligaría a recargarla.
      resolved = Resolution(as_proposal(row, area), "resolved")
      await session.rollback()
      return resolved
    outcome: Outcome = await apply(session, row, embedding) if decision == "apply" else "discarded"
    row.status = { "applied": "applied", "discarded": "discarded" }.get(outcome, "rejected")
    row.resolved_at = datetime.now(UTC)
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return Resolution(as_proposal(row, area), outcome)
