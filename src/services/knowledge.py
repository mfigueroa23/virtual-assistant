from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.agents import Agents
from src.models.areas import Area
from src.models.faq_categories import FaqCategory
from src.models.faqs import Faq
from src.models.knowledge import KnowledgeProposal
from src.services.property import get_property
from src.utils.exceptions.database import DatabaseUnavailableError

# «Conocimiento» administra las áreas de las tres audiencias (D4 del plan 011).
AUDIENCIAS = ("externo", "interno", "personal")
# Área interno del sub-agente que administra el conocimiento (D3 del plan 009).
CONOCIMIENTO = "Conocimiento"

# Prompt base del sub-agente de un área nueva (D7 del plan 011): igual para todas, con una regla por audiencia.
PROMPT_BASE = (
  "Eres el especialista del área «{nombre}» del asistente de Marco Figueroa. Tu área cubre: {descripcion}\n\n"
  "Cómo responder:\n"
  "- Usa SIEMPRE search_faq antes de responder; busca solo en la información de tu área.\n"
  "- Responde solo con lo que encuentres. Si no hay información, dilo con claridad; no inventes ni supongas.\n"
  "- Responde en el idioma de la consulta, breve.\n"
  "- "
)
REGLAS = {
  "externo": (
    "Lo puede leer cualquier visitante del portafolio o contacto de WhatsApp: habla de Marco en tercera persona y no "
    "compartas datos personales."
  ),
  "personal": (
    "Lo puede leer cualquier persona que escriba al WhatsApp de Marco: no compartas datos de contacto, direcciones ni "
    "información de terceros."
  ),
  "interno": "Respondes a Marco en Google Chat: puedes dar el detalle interno de tu área.",
}

Action = Literal["create", "update", "delete", "category", "area"]
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
  audiencia: str

@dataclass(frozen = True)
class KnowledgeHit:
  id: int
  area: str
  category: str
  question: str
  answer: str
  distance: float
  audiencia: str

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
  # Audiencia del área; vacía solo en los fakes de antes de la spec 011 (U-T9).
  audiencia: str = ""
  # Descripción del área que se propone crear (action = "area").
  description: str | None = None

@dataclass(frozen = True)
class Resolution:
  proposal: Proposal
  outcome: Outcome

def as_proposal(row: KnowledgeProposal, area: str | None, audiencia: str | None) -> Proposal:
  # Una propuesta de área pendiente aún no tiene área: el nombre y la audiencia salen de sus columnas (D6 del plan 011).
  return Proposal(
    row.id, row.origin, row.action, area or row.area_name or "", row.category or "", row.faq_id, row.question, row.answer,
    row.previous_question, row.previous_answer, row.status, audiencia or row.area_audiencia or "", row.area_description
  )

async def list_knowledge(session: AsyncSession, audiencias: Sequence[str] = AUDIENCIAS) -> list[AreaSummary]:
  """Áreas de las audiencias con sus categorías y cuántas FAQs tiene cada una (RF-08 de la spec 011)."""
  try:
    rows = (await session.execute(
      select(Area.name, Area.description, Area.audiencia, FaqCategory.name, func.count(Faq.id))
      .outerjoin(FaqCategory, FaqCategory.area_id == Area.id)
      .outerjoin(Faq, Faq.category_id == FaqCategory.id)
      .where(Area.audiencia.in_(audiencias))
      .group_by(Area.id, Area.name, Area.description, Area.audiencia, FaqCategory.name)
      .order_by(Area.name, FaqCategory.name)
    )).tuples().all()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  areas: dict[str, AreaSummary] = {}
  for area, description, audiencia, category, faqs in rows:
    summary = areas.setdefault(area, AreaSummary(area, description, [], audiencia))
    if category is not None:
      summary.categories.append(CategorySummary(category, faqs))
  return list(areas.values())

async def search_knowledge(
  session: AsyncSession, vector: list[float], audiencias: Sequence[str] = AUDIENCIAS
) -> list[KnowledgeHit]:
  """Como search_faqs, pero en las áreas de las audiencias y con el id de cada FAQ para editarla o borrarla (RF-09)."""
  try:
    distance = Faq.embedding.cosine_distance(vector)
    k = int(await get_property(session, "top_k"))
    max_distance = float(await get_property(session, "max_distance"))
    rows = (await session.execute(
      select(Faq.id, Area.name, FaqCategory.name, Faq.question, Faq.answer, distance.label("distance"), Area.audiencia)
      .join(FaqCategory, FaqCategory.id == Faq.category_id)
      .join(Area, Area.id == FaqCategory.area_id)
      .where(Area.audiencia.in_(audiencias), Faq.embedding.is_not(None), distance < max_distance)
      .order_by(distance)
      .limit(k)
    )).tuples().all()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return [KnowledgeHit(*row) for row in rows]

def group_areas(rows: Iterable[tuple[str, str]]) -> str:
  """Una línea por audiencia con sus áreas, en el orden de rows (RF-12 de la spec 011)."""
  grouped: dict[str, list[str]] = {}
  for audiencia, name in rows:
    grouped.setdefault(audiencia, []).append(name)
  return "".join(f"\n- {audiencia}: {', '.join(names)}" for audiencia, names in grouped.items())

async def areas_text(session: AsyncSession, audiencias: Sequence[str] = AUDIENCIAS) -> str:
  rows = (await session.execute(
    select(Area.audiencia, Area.name).where(Area.audiencia.in_(audiencias)).order_by(Area.audiencia, Area.name)
  )).tuples().all()
  return group_areas(rows)

async def create_proposal(
  session: AsyncSession,
  origin: Origin,
  action: Action,
  area: str = "",
  category: str = "",
  question: str = "",
  answer: str = "",
  faq_id: int | None = None,
  turn_id: str | None = None,
  audiencias: Sequence[str] = AUDIENCIAS
) -> Proposal | str:
  """Guarda un cambio pendiente de confirmar; si no valida devuelve el motivo, sin guardar nada (RF-07 a RF-10).

  Solo acepta áreas de `audiencias`: las propuestas desde consultas la limitan a personal (D8 del plan 011).
  """
  question, answer, category = question.strip(), answer.strip(), category.strip()
  try:
    previous: tuple[str, str] | None = None
    audiencia: str | None = None
    if action in ("update", "delete"):
      # La FAQ manda el área y la categoría: editar no la cambia de categoría.
      row = (await session.execute(
        select(Area.id, Area.name, Area.audiencia, FaqCategory.name, Faq.question, Faq.answer)
        .join(FaqCategory, FaqCategory.id == Faq.category_id).join(Area, Area.id == FaqCategory.area_id)
        .where(Faq.id == faq_id, Area.audiencia.in_(audiencias))
      )).tuples().one_or_none()
      if row is None:
        return f"No existe una pregunta frecuente con id {faq_id}. Búscala primero con buscar_conocimiento."
      area_id, area, audiencia, category, *current = row
      previous = (current[0], current[1])
      if action == "update":
        question, answer = question or previous[0], answer or previous[1]
        if (question, answer) == previous:
          return "La pregunta y la respuesta quedarían iguales: indica qué cambia."
      else:
        question, answer = "", ""
    else:
      found = (await session.execute(
        select(Area.id, Area.audiencia).where(Area.name == area.strip(), Area.audiencia.in_(audiencias))
      )).tuples().one_or_none()
      if found is None:
        return f"El área «{area}» no existe. Áreas disponibles por audiencia:{await areas_text(session, audiencias)}"
      area_id, audiencia = found
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
  return as_proposal(proposal, area, audiencia)

async def get_proposal(session: AsyncSession, proposal_id: str) -> Proposal | None:
  try:
    row = (await session.execute(
      select(KnowledgeProposal, Area.name, Area.audiencia).outerjoin(Area, Area.id == KnowledgeProposal.area_id)
      .where(KnowledgeProposal.id == proposal_id)
    )).tuples().one_or_none()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return as_proposal(*row) if row else None

async def proposals_of_turn(session: AsyncSession, turn_id: str) -> list[Proposal]:
  """Propuestas del turno del «Agente Interno», en orden, para armar sus tarjetas (D2)."""
  try:
    rows = (await session.execute(
      select(KnowledgeProposal, Area.name, Area.audiencia).outerjoin(Area, Area.id == KnowledgeProposal.area_id)
      .where(KnowledgeProposal.turn_id == turn_id).order_by(KnowledgeProposal.created_at)
    )).tuples().all()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return [as_proposal(*row) for row in rows]

def base_prompt(audiencia: str, name: str, description: str) -> str:
  # Concatenado y no con format: la descripción puede traer llaves.
  return PROMPT_BASE.replace("{nombre}", name).replace("{descripcion}", description) + REGLAS[audiencia]

async def create_area_proposal(
  session: AsyncSession, name: str, audiencia: str, description: str, turn_id: str | None = None
) -> Proposal | str:
  """Guarda la creación de un área pendiente de confirmar; si no valida devuelve el motivo, sin guardar nada (RF-14, RF-19, RF-21)."""
  name, description = name.strip(), description.strip()
  missing = [label for label, value in (("el nombre", name), ("la descripción", description)) if not value]
  if missing:
    return f"Falta {' y '.join(missing)} del área."
  if audiencia not in AUDIENCIAS:
    return f"La audiencia «{audiencia}» no es válida. Audiencias válidas: {', '.join(AUDIENCIAS)}."
  try:
    # Los nombres son únicos entre las tres audiencias (RF-19).
    existing = await session.scalar(select(Area.audiencia).where(Area.name == name))
    if existing is not None:
      return f"El área «{name}» ya existe en la audiencia {existing}."
    proposal = KnowledgeProposal(
      id = str(uuid4()), turn_id = turn_id, origin = "interno", action = "area", area_name = name,
      area_description = description, area_audiencia = audiencia, status = "pending"
    )
    session.add(proposal)
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return as_proposal(proposal, None, None)

async def category_id(session: AsyncSession, area_id: int, name: str) -> int | None:
  return await session.scalar(select(FaqCategory.id).where(FaqCategory.area_id == area_id, FaqCategory.name == name))

async def apply_area(session: AsyncSession, row: KnowledgeProposal) -> Outcome:
  """Crea el área y su sub-agente «Agente <nombre>», y guarda el área creada en la propuesta (D6, D10 del plan 011)."""
  name, description, audiencia = cast(str, row.area_name), cast(str, row.area_description), cast(str, row.area_audiencia)
  agent = f"Agente {name}"
  # agents.name también es único: un área «Externo» chocaría con el coordinador «Agente Externo» (D10).
  if await session.scalar(select(Area.id).where(Area.name == name)) is not None or await session.scalar(
    select(Agents.id).where(Agents.name == agent)
  ) is not None:
    return "duplicate"
  area = Area(name = name, description = description, audiencia = audiencia)
  session.add(area)
  await session.flush()
  session.add(Agents(name = agent, prompt = base_prompt(audiencia, name, description), area_id = area.id))
  row.area_id = area.id
  return "applied"

async def apply(session: AsyncSession, row: KnowledgeProposal, embedding: list[float] | None) -> Outcome:
  if row.action == "area":
    return await apply_area(session, row)
  # ck_knowledge_proposals_area asegura el área y la categoría de las propuestas de FAQs y categorías.
  area_id, name = cast(int, row.area_id), cast(str, row.category)
  if row.action == "category":
    if await category_id(session, area_id, name) is not None:
      return "duplicate"
    session.add(FaqCategory(area_id = area_id, name = name))
    return "applied"
  if row.action == "create":
    category = await category_id(session, area_id, name)
    if category is None:
      created = FaqCategory(area_id = area_id, name = name)
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
    # Una propuesta de área pendiente no tiene área: as_proposal toma sus columnas propias.
    area, audiencia = (await session.execute(
      select(Area.name, Area.audiencia).where(Area.id == row.area_id)
    )).tuples().one_or_none() or (None, None)
    if row.status != "pending":
      # Antes del rollback, que expira la fila y obligaría a recargarla.
      resolved = Resolution(as_proposal(row, area, audiencia), "resolved")
      await session.rollback()
      return resolved
    outcome: Outcome = await apply(session, row, embedding) if decision == "apply" else "discarded"
    row.status = { "applied": "applied", "discarded": "discarded" }.get(outcome, "rejected")
    row.resolved_at = datetime.now(UTC)
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  return Resolution(as_proposal(row, area, audiencia), outcome)
