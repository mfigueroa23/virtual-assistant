import asyncio
import logging
from datetime import UTC, datetime, timedelta
import httpx
import psycopg
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from src.agents.context import CoordinatorContext
from src.agents.llm import LLM
from src.database.session import SessionLocal
from src.interfaces.whatsapp import MessageContent, WhatsappEvent, WhatsappMessage
from src.services.assistant import ask
from src.services.attachments import MAX_BYTES, Attachment, read_attachments, transcribe_audio
from src.services.property import get_property
from src.services.whatsapp import (
  Batch, QueuedMessage, Wait, WhatsappSettings, add_echo, add_inbound, claim_batch, current_epoch, defer_batch,
  defer_conversation, finish_batch, pending_phones, read_settings, register_event
)
from src.services.ycloud import download_media, mark_as_read, send_text, split_text
from src.utils.exceptions.agents import AgentNotFoundError
from src.utils.exceptions.attachments import AttachmentTooLargeError
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError

logger = logging.getLogger(__name__)

COORDINADOR = "Agente WhatsApp"
AUDIENCIA = "externo"
ENTRANTE = "whatsapp.inbound_message.received"
ECHO = "whatsapp.smb.message.echoes"
ESTADO = "whatsapp.message.updated"
# Solo estos tipos generan respuesta; el resto queda como una línea en el historial (RF-20).
RESPONDEN = { "text", "image", "document", "audio" }
# Margen sobre el tiempo máximo para dar por caído un proceso que tomó un lote (D3).
MARGEN = 60.0
# Al arrancar solo se responden los lotes recientes: uno más viejo probablemente ya lo atendió Marco (D13).
RECUPERAR = timedelta(minutes = 30)

# Lotes en curso: mantiene la referencia para que no los elimine el recolector y permite cancelarlos al apagar.
pending: set[asyncio.Task[None]] = set()

def thread_key(phone: str) -> str:
  # Prefijo propio: el historial de WhatsApp no se mezcla con el del chat web ni con el de Google Chat (D7).
  return f"whatsapp:{phone}"

def describe(message: MessageContent) -> str | None:
  """Texto que se guarda del mensaje: el cuerpo, la leyenda de un medio o una línea para lo que no se lee."""
  match message.type:
    case "text":
      return message.text.body if message.text else ""
    case "image" | "document" | "audio":
      media = message.image or message.document or message.audio
      return media.caption if media and media.caption else None
    case "video":
      caption = message.video.caption if message.video and message.video.caption else ""
      return f"[Video] {caption}".strip()
    case "sticker":
      return "[Sticker]"
    case "location":
      place = message.location or {}
      name = ", ".join(str(place[key]) for key in ("name", "address") if place.get(key))
      return f"[Ubicación: {name or f'{place.get('latitude')}, {place.get('longitude')}'}]"
    case "contacts":
      names = ", ".join(contact.get("name", {}).get("formatted_name", "sin nombre") for contact in message.contacts or [])
      return f"[Tarjeta de contacto: {names}]"
    case "reaction":
      emoji = message.reaction.emoji if message.reaction else None
      return f"[Reaccionó con {emoji}]" if emoji else "[Quitó una reacción]"
    case _:
      return f"[Mensaje de tipo {message.type}]"

def to_queued(message: MessageContent, direction: str, now: datetime | None = None) -> QueuedMessage:
  created = message.create_time if isinstance(message, WhatsappMessage) else None
  sent_at = message.send_time or created or now or datetime.now(UTC)
  media = (message.image or message.document or message.audio) if message.type in ("image", "document", "audio") else None
  name = (media.filename if media else None) or { "image": "imagen", "audio": "nota de voz" }.get(message.type)
  return QueuedMessage(
    message.id, direction, sent_at, message.type, describe(message),
    media.link if media else None, media.mime_type if media else None, name if media else None
  )

def responds(message: QueuedMessage) -> bool:
  return message.type in RESPONDEN

def schedule(graph: CompiledStateGraph[MessagesState, CoordinatorContext], phone: str) -> None:
  # Una tarea por mensaje: si ya hay otra para la conversación, la toma del lote en la BD evita responder dos veces (D3).
  task = asyncio.create_task(process_conversation(graph, phone))
  pending.add(task)
  task.add_done_callback(pending.discard)

async def handle_event(graph: CompiledStateGraph[MessagesState, CoordinatorContext], session: AsyncSession, event: WhatsappEvent) -> None:
  """Registra el evento en la BD y, si es un mensaje del contacto, agenda su respuesta; no espera al asistente."""
  now = datetime.now(UTC)
  # El id se registra en la misma transacción que el mensaje: si algo falla, el reintento de YCloud no se pierde.
  if not await register_event(session, event.id, now):
    return
  inbound = event.whatsapp_inbound_message
  outbound = event.whatsapp_message
  if event.type == ENTRANTE and inbound is not None and not inbound.group_id:
    settings = await read_settings(session)
    queue = await add_inbound(session, inbound.from_, inbound.to, to_queued(inbound, "inbound", now), now, settings.grouping)
    await session.commit()
    if queue:
      schedule(graph, inbound.from_)
    return
  if event.type == ECHO and outbound is not None:
    settings = await read_settings(session)
    await add_echo(session, outbound.to, outbound.from_, to_queued(outbound, "echo", now), settings.pause)
  elif event.type == ESTADO and outbound is not None and outbound.status == "failed":
    logger.warning("WhatsApp no entregó el mensaje %s: %s %s", outbound.id, outbound.error_code, outbound.error_message)
  await session.commit()

def history_line(message: QueuedMessage) -> str:
  # Los medios del historial no se leen: basta con su tipo y su leyenda (D11).
  caption = f" {message.text}" if message.text else ""
  match message.type:
    case "image":
      return f"[Imagen]{caption}"
    case "document":
      return f"[Documento: {message.media_name}]{caption}"
    case "audio":
      return f"[Nota de voz]{caption}"
    case _:
      return message.text or ""

def history_message(message: QueuedMessage) -> AnyMessage:
  # El asistente habla como Marco: sus mensajes desde la app son la voz del asistente en el historial.
  line = history_line(message)
  return AIMessage(line) if message.direction == "echo" else HumanMessage(line)

async def read_audio(message: QueuedMessage, api_key: str, http: httpx.AsyncClient, llm: BaseChatModel) -> str:
  if not message.media_link:
    return "[Nota de voz · no se pudo leer]"
  try:
    data = await download_media(api_key, message.media_link, http)
    return f"[Nota de voz]\n{await transcribe_audio(data, message.media_mime or 'audio/ogg', llm)}"
  except AttachmentTooLargeError:
    return f"[Nota de voz · supera los {MAX_BYTES // (1024 * 1024)} MB, no se leyó]"
  except Exception as exc:
    logger.warning("No se pudo leer la nota de voz de WhatsApp: %s", type(exc).__name__)
    return "[Nota de voz · no se pudo leer]"

async def read_media(message: QueuedMessage, api_key: str, http: httpx.AsyncClient, llm: BaseChatModel) -> str:
  # Imágenes y documentos usan la misma lectura que Google Chat; un fallo deja su línea y no corta el lote (RF-19).
  if message.type == "audio":
    body = await read_audio(message, api_key, http, llm)
  elif not message.media_link:
    body = f"[Archivo adjunto: {message.media_name} · no se pudo leer]"
  else:
    async def download(link: str) -> bytes:
      return await download_media(api_key, link, http)

    attachment = Attachment(message.media_name or "archivo", message.media_mime or "", message.media_link)
    body = await read_attachments([attachment], download, llm)
  return f"{message.text}\n{body}" if message.text else body

async def build_text(session: AsyncSession, batch: Batch, api_key: str, http: httpx.AsyncClient) -> str:
  """Texto del turno: los mensajes del lote en orden, con los archivos leídos y los audios transcritos."""
  llm = await LLM.create(session) if any(message.media_link for message in batch.messages) else None

  async def line(message: QueuedMessage) -> str:
    if llm is not None and message.type in ("image", "document", "audio"):
      return await read_media(message, api_key, http, llm.llm)
    return history_line(message)

  return "\n\n".join(await asyncio.gather(*(line(message) for message in batch.messages)))

async def forget_answer(graph: CompiledStateGraph[MessagesState, CoordinatorContext], phone: str) -> None:
  """Quita del historial la última respuesta del asistente porque el contacto nunca la recibió (D5)."""
  config: RunnableConfig = { "configurable": { "thread_id": thread_key(phone) } }
  state = await graph.aget_state(config)
  messages = state.values.get("messages", [])
  if messages and isinstance(messages[-1], AIMessage) and messages[-1].id:
    await graph.aupdate_state(config, { "messages": [RemoveMessage(id = messages[-1].id)] }, as_node = "coordinator")

async def release(batch: Batch, ids: list[str]) -> None:
  try:
    async with SessionLocal() as session:
      await finish_batch(session, batch.phone, ids)
  except Exception as exc:
    # Si no se libera, el lote se da por caído al pasar el tiempo máximo más el margen (D3).
    logger.error("No se pudo liberar el lote de WhatsApp: %s", type(exc).__name__)

async def answer_batch(graph: CompiledStateGraph[MessagesState, CoordinatorContext], batch: Batch, settings: WhatsappSettings) -> None:
  """Responde el lote como Marco; ante cualquier fallo no envía nada y lo deja en el log, sin el contenido (D12)."""
  ids = [message.id for message in batch.messages + batch.history]
  if not any(responds(message) for message in batch.messages):
    # Solo stickers, reacciones y similares: no se responde y quedan para el historial del próximo lote (RF-21).
    try:
      async with SessionLocal() as session:
        await defer_batch(session, batch.phone, [message.id for message in batch.messages])
    except Exception as exc:
      logger.error("No se pudo guardar el lote de WhatsApp para el historial: %s", type(exc).__name__)
    return
  async with httpx.AsyncClient(timeout = 20) as http:
    try:
      async with SessionLocal() as session, asyncio.timeout(settings.maximum):
        api_key = await get_property(session, "ycloud_api_key")
        text = await build_text(session, batch, api_key, http)
        previous = [history_message(message) for message in batch.history]
        respuesta = await ask(graph, session, COORDINADOR, AUDIENCIA, thread_key(batch.phone), text, previous = previous)
    except TimeoutError:
      logger.warning("Se superó el tiempo máximo de %s segundos para responder en WhatsApp", settings.maximum)
      return await release(batch, ids)
    except (PropertyNotFoundError, AgentNotFoundError) as exc:
      logger.error("WhatsApp mal configurado: %s", exc)
      return await release(batch, ids)
    except (DatabaseUnavailableError, psycopg.OperationalError) as exc:
      logger.error("Base de datos no disponible al responder en WhatsApp: %s", type(exc).__name__)
      return await release(batch, ids)
    except Exception as exc:
      # Solo el tipo de error: el mensaje de la excepción podría incluir el texto del contacto.
      logger.error("Ha ocurrido un error al responder en WhatsApp: %s", type(exc).__name__)
      return await release(batch, ids)
    if not respuesta.strip():
      logger.warning("El asistente devolvió una respuesta vacía en WhatsApp, no se envía nada")
      return await release(batch, ids)
    try:
      async with SessionLocal() as session:
        epoch = await current_epoch(session, batch.phone)
      if epoch != batch.epoch:
        # Marco respondió mientras el asistente preparaba la respuesta: la conversación es suya (RF-25).
        await forget_answer(graph, batch.phone)
        return await release(batch, ids)
      for part in split_text(respuesta):
        await send_text(api_key, batch.business_phone, batch.phone, part, http)
    except httpx.HTTPError as exc:
      logger.error("YCloud no aceptó la respuesta de WhatsApp: %s", type(exc).__name__)
      await forget_answer(graph, batch.phone)
      return await release(batch, ids)
    except Exception as exc:
      logger.error("Ha ocurrido un error al enviar la respuesta de WhatsApp: %s", type(exc).__name__)
      return await release(batch, ids)
    try:
      inbound = [message for message in batch.messages if message.direction == "inbound"]
      await mark_as_read(api_key, inbound[-1].id, http)
    except Exception as exc:
      logger.warning("No se pudo marcar como leído el mensaje de WhatsApp: %s", type(exc).__name__)
  await release(batch, ids)

async def process_conversation(graph: CompiledStateGraph[MessagesState, CoordinatorContext], phone: str) -> None:
  # Espera la agrupación, toma el lote y vuelve a mirar: así responde también lo que llegó mientras respondía (D2).
  while True:
    try:
      async with SessionLocal() as session:
        settings = await read_settings(session)
        result = await claim_batch(session, phone, datetime.now(UTC), timedelta(seconds = settings.maximum + MARGEN))
    except Exception as exc:
      logger.error("No se pudo tomar el lote de WhatsApp: %s", type(exc).__name__)
      return
    if result is None:
      return
    if isinstance(result, Wait):
      await asyncio.sleep(result.seconds)
      continue
    await answer_batch(graph, result, settings)

async def recover(graph: CompiledStateGraph[MessagesState, CoordinatorContext]) -> None:
  """Al arrancar, retoma los lotes que quedaron sin responder; los muy viejos solo quedan para el historial (D13)."""
  try:
    async with SessionLocal() as session:
      conversations = await pending_phones(session)
    limit = datetime.now(UTC) - RECUPERAR
    for phone, last in conversations:
      if last >= limit:
        schedule(graph, phone)
        continue
      async with SessionLocal() as session:
        await defer_conversation(session, phone)
  except Exception as exc:
    # Un fallo aquí no impide arrancar: los lotes se retoman con el siguiente mensaje del contacto.
    logger.error("No se pudieron recuperar los lotes de WhatsApp pendientes: %s", type(exc).__name__)

async def cancel_pending() -> int:
  # Los lotes en curso se pierden al apagar y se retoman al arrancar si siguen siendo recientes; queda en el log.
  tasks = list(pending)
  for task in tasks:
    task.cancel()
  await asyncio.gather(*tasks, return_exceptions = True)
  pending.clear()
  if tasks:
    logger.warning("Se cancelaron %s lotes de WhatsApp en curso al apagar", len(tasks))
  return len(tasks)
