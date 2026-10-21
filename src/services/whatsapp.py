import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.whatsapp import WhatsappConversation, WhatsappEvent, WhatsappMessage, WhatsappNotice
from src.services.property import get_property
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError

logger = logging.getLogger(__name__)

PAUSA_POR_DEFECTO = 12.0
AGRUPACION_POR_DEFECTO = 8.0
MAXIMO_POR_DEFECTO = 120.0
# YCloud deja de reintentar a las ~6 h: una semana de ids basta para descartar duplicados.
RETENCION_EVENTOS = timedelta(days = 7)
# Mientras otro proceso responde el lote, se vuelve a mirar cada segundo.
ESPERA_OCUPADA = 1.0

@dataclass(frozen = True)
class WhatsappSettings:
  pause: timedelta
  grouping: float
  maximum: float

@dataclass(frozen = True)
class QueuedMessage:
  id: str
  direction: str
  sent_at: datetime
  type: str
  text: str | None = None
  media_link: str | None = None
  media_mime: str | None = None
  media_name: str | None = None

@dataclass(frozen = True)
class Wait:
  seconds: float

@dataclass(frozen = True)
class Batch:
  phone: str
  business_phone: str
  epoch: int
  # Mensajes del contacto que se responden, en orden.
  messages: list[QueuedMessage]
  # Mensajes ya ocurridos que se vuelcan al historial antes del turno (D4).
  history: list[QueuedMessage]
  # Nombre de perfil del contacto, para el aviso a Marco.
  contact_name: str | None = None

@dataclass(frozen = True)
class NoticeTarget:
  """Conversación a la que pertenece un aviso de Google Chat."""
  phone: str
  business_phone: str
  contact_name: str | None
  last_inbound_at: datetime | None
  # Preguntas de «Te consulta» del aviso: solo esas respuestas proponen una FAQ (RF-20, RF-25 de la spec 009).
  questions: tuple[str, ...] = ()

async def channel_enabled(session: AsyncSession) -> bool:
  # Si falta la property el canal queda apagado: hay que habilitarlo a propósito.
  try:
    return (await get_property(session, "whatsapp_enabled")).strip().lower() == "true"
  except PropertyNotFoundError:
    return False

async def read_float(session: AsyncSession, key: str, default: float) -> float:
  try:
    return float(await get_property(session, key))
  except PropertyNotFoundError:
    return default

async def read_settings(session: AsyncSession) -> WhatsappSettings:
  # Sin caché: un cambio en las properties se aplica desde el siguiente evento.
  return WhatsappSettings(
    pause = timedelta(hours = await read_float(session, "whatsapp_pause_hours", PAUSA_POR_DEFECTO)),
    grouping = await read_float(session, "whatsapp_grouping_seconds", AGRUPACION_POR_DEFECTO),
    maximum = await read_float(session, "whatsapp_timeout", MAXIMO_POR_DEFECTO)
  )

def next_pause(current: datetime | None, sent_at: datetime, window: timedelta) -> datetime:
  # Cada mensaje de Marco renueva la ventana desde ese mensaje, pero uno atrasado no la acorta.
  until = sent_at + window
  return until if current is None or until > current else current

def latest_inbound(current: datetime | None, sent_at: datetime) -> datetime:
  # Un mensaje atrasado no mueve hacia atrás la ventana de 24 h (D4).
  return sent_at if current is None or sent_at > current else current

def claim_decision(
  processing_since: datetime | None, batch_due_at: datetime | None, paused_until: datetime | None,
  has_pending: bool, now: datetime, stale: timedelta
) -> Wait | Literal["claim", "defer"] | None:
  """Qué hacer con el lote de una conversación: nada, esperar, tomarlo o pasarlo al historial (D2, D3)."""
  if not has_pending:
    return None
  if paused_until is not None and paused_until > now:
    return "defer"
  # Un proceso que no terminó en el tiempo máximo más un margen se da por caído y su lote se libera.
  if processing_since is not None and processing_since > now - stale:
    return Wait(ESPERA_OCUPADA)
  if batch_due_at is not None and batch_due_at > now:
    return Wait((batch_due_at - now).total_seconds())
  return "claim"

def queued(row: WhatsappMessage) -> QueuedMessage:
  return QueuedMessage(row.id, row.direction, row.sent_at, row.type, row.text, row.media_link, row.media_mime, row.media_name)

async def register_event(session: AsyncSession, event_id: str, now: datetime) -> bool:
  """Registra el id del evento en la transacción en curso; False si ya se había recibido."""
  try:
    inserted = await session.execute(
      insert(WhatsappEvent).values(id = event_id).on_conflict_do_nothing().returning(WhatsappEvent.id)
    )
    await session.execute(delete(WhatsappEvent).where(WhatsappEvent.received_at < now - RETENCION_EVENTOS))
    return inserted.scalar_one_or_none() is not None
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def lock_conversation(session: AsyncSession, phone: str, business_phone: str) -> WhatsappConversation:
  # FOR UPDATE serializa los eventos y los lotes de una misma conversación entre réplicas.
  await session.execute(
    insert(WhatsappConversation).values(phone = phone, business_phone = business_phone).on_conflict_do_nothing()
  )
  conversation = await session.get(WhatsappConversation, phone, with_for_update = True, populate_existing = True)
  if conversation is None:
    raise DatabaseUnavailableError(f"No se pudo bloquear la conversación {phone}")
  return conversation

async def save_message(session: AsyncSession, phone: str, message: QueuedMessage, status: str) -> None:
  await session.execute(insert(WhatsappMessage).values(
    id = message.id, phone = phone, direction = message.direction, status = status, sent_at = message.sent_at,
    type = message.type, text = message.text, media_link = message.media_link, media_mime = message.media_mime,
    media_name = message.media_name
  ).on_conflict_do_nothing())

async def add_inbound(
  session: AsyncSession, phone: str, business_phone: str, message: QueuedMessage, now: datetime, grouping: float,
  contact_name: str | None = None
) -> bool:
  """Encola el mensaje del contacto; True si hay que agendar su lote (la conversación no está en pausa)."""
  try:
    conversation = await lock_conversation(session, phone, business_phone)
    conversation.business_phone = business_phone
    # Si el contacto cambia su nombre de perfil, el aviso usa el último (D7).
    if contact_name:
      conversation.contact_name = contact_name
    conversation.last_inbound_at = latest_inbound(conversation.last_inbound_at, message.sent_at)
    if conversation.paused_until is not None and conversation.paused_until > now:
      # Durante la pausa solo se guarda para el historial, sin respuesta.
      await save_message(session, phone, message, "history")
      return False
    await save_message(session, phone, message, "pending")
    # Cada mensaje nuevo reinicia la espera de agrupación.
    conversation.batch_due_at = now + timedelta(seconds = grouping)
    return True
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def add_echo(session: AsyncSession, phone: str, business_phone: str, message: QueuedMessage, window: timedelta) -> None:
  """Registra un mensaje de Marco: pausa la conversación y descarta el lote que estaba por responderse (D6)."""
  try:
    conversation = await lock_conversation(session, phone, business_phone)
    conversation.paused_until = next_pause(conversation.paused_until, message.sent_at, window)
    conversation.epoch += 1
    conversation.batch_due_at = None
    await session.execute(
      update(WhatsappMessage).where(WhatsappMessage.phone == phone, WhatsappMessage.status == "pending")
      .values(status = "history")
    )
    await save_message(session, phone, message, "history")
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def messages_with(session: AsyncSession, phone: str, status: str) -> list[QueuedMessage]:
  rows = await session.scalars(
    select(WhatsappMessage).where(WhatsappMessage.phone == phone, WhatsappMessage.status == status)
    .order_by(WhatsappMessage.sent_at, WhatsappMessage.id)
  )
  return [queued(row) for row in rows]

async def claim_batch(session: AsyncSession, phone: str, now: datetime, stale: timedelta) -> Wait | Batch | None:
  """Toma el lote vencido de la conversación en una transacción corta, o dice cuánto esperar."""
  try:
    conversation = await session.get(WhatsappConversation, phone, with_for_update = True, populate_existing = True)
    if conversation is None:
      return None
    pending = await messages_with(session, phone, "pending")
    decision = claim_decision(
      conversation.processing_since, conversation.batch_due_at, conversation.paused_until, bool(pending), now, stale
    )
    if decision is None or decision == "defer":
      if decision == "defer":
        await session.execute(
          update(WhatsappMessage).where(WhatsappMessage.phone == phone, WhatsappMessage.status == "pending")
          .values(status = "history")
        )
      conversation.batch_due_at = None
      await session.commit()
      return None
    if isinstance(decision, Wait):
      await session.commit()
      return decision
    # Un lote que quedó en processing es de un proceso caído: se descarta como cualquier lote fallido (D12).
    await session.execute(delete(WhatsappMessage).where(WhatsappMessage.phone == phone, WhatsappMessage.status == "processing"))
    history = await messages_with(session, phone, "history")
    await session.execute(
      update(WhatsappMessage).where(WhatsappMessage.id.in_([message.id for message in pending])).values(status = "processing")
    )
    conversation.processing_since = now
    conversation.batch_due_at = None
    batch = Batch(phone, conversation.business_phone, conversation.epoch, pending, history, conversation.contact_name)
    await session.commit()
    return batch
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def pause_conversation(session: AsyncSession, phone: str, now: datetime, window: timedelta) -> None:
  """Pausa la conversación al derivarla a Marco, sin subir epoch ni guardar mensajes (D4)."""
  try:
    conversation = await session.get(WhatsappConversation, phone, with_for_update = True, populate_existing = True)
    if conversation is None:
      return
    conversation.paused_until = next_pause(conversation.paused_until, now, window)
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def save_notice(session: AsyncSession, thread: str, phone: str, questions: Sequence[str] = ()) -> None:
  """Recuerda el hilo de un aviso para reconocer las respuestas de Marco en él, también tras un reinicio (D1, D3)."""
  try:
    # Las preguntas, una por línea, dicen a qué consultas responde el hilo (D10 del plan 009).
    await session.execute(insert(WhatsappNotice).values(
      thread = thread, phone = phone, questions = "\n".join(questions) or None
    ).on_conflict_do_nothing())
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def find_notice(session: AsyncSession, thread: str) -> NoticeTarget | None:
  try:
    found = (await session.execute(
      select(WhatsappConversation, WhatsappNotice.questions)
      .join(WhatsappNotice, WhatsappNotice.phone == WhatsappConversation.phone)
      .where(WhatsappNotice.thread == thread)
    )).tuples().one_or_none()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
  if found is None:
    return None
  row, questions = found
  return NoticeTarget(
    row.phone, row.business_phone, row.contact_name, row.last_inbound_at, tuple((questions or "").splitlines())
  )

async def add_marco_reply(session: AsyncSession, phone: str, message: QueuedMessage) -> None:
  """Guarda lo que Marco respondió desde Google Chat y reanuda el asistente en la conversación (D5, D6)."""
  try:
    conversation = await session.get(WhatsappConversation, phone, with_for_update = True, populate_existing = True)
    if conversation is None:
      return
    # Sin subir epoch: el asistente sigue atendiendo y el mensaje entra al historial en el próximo lote.
    conversation.paused_until = None
    await save_message(session, phone, message, "history")
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def current_epoch(session: AsyncSession, phone: str) -> int:
  try:
    return await session.scalar(select(WhatsappConversation.epoch).where(WhatsappConversation.phone == phone)) or 0
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def finish_batch(session: AsyncSession, phone: str, ids: list[str]) -> None:
  """Saca de la cola los mensajes que ya quedaron en el historial (o que se descartan) y libera la conversación."""
  try:
    await session.execute(delete(WhatsappMessage).where(WhatsappMessage.id.in_(ids)))
    await session.execute(update(WhatsappConversation).where(WhatsappConversation.phone == phone).values(processing_since = None))
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def defer_batch(session: AsyncSession, phone: str, ids: list[str]) -> None:
  """Deja los mensajes para el historial del próximo lote, sin responderlos, y libera la conversación."""
  try:
    await session.execute(update(WhatsappMessage).where(WhatsappMessage.id.in_(ids)).values(status = "history"))
    await session.execute(update(WhatsappConversation).where(WhatsappConversation.phone == phone).values(processing_since = None))
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def pending_phones(session: AsyncSession) -> list[tuple[str, datetime]]:
  """Conversaciones con un lote sin terminar y la hora de su último mensaje, para retomarlas al arrancar (D13)."""
  try:
    rows = await session.execute(
      select(WhatsappMessage.phone, func.max(WhatsappMessage.sent_at))
      .where(WhatsappMessage.status.in_(["pending", "processing"])).group_by(WhatsappMessage.phone)
    )
    return [(phone, last) for phone, last in rows.tuples()]
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc

async def defer_conversation(session: AsyncSession, phone: str) -> None:
  """Pasa al historial un lote demasiado viejo para responderlo y libera la conversación (D13)."""
  try:
    await session.execute(
      update(WhatsappMessage).where(WhatsappMessage.phone == phone, WhatsappMessage.status.in_(["pending", "processing"]))
      .values(status = "history")
    )
    await session.execute(
      update(WhatsappConversation).where(WhatsappConversation.phone == phone).values(processing_since = None, batch_due_at = None)
    )
    await session.commit()
  except (SQLAlchemyError, OSError) as exc:
    raise DatabaseUnavailableError(str(exc)) from exc
