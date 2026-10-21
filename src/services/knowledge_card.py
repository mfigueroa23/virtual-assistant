import html
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from src.agents.llm import LLM
from src.interfaces.google_chat import AddonEvent, ChatMessage, chat_reply, update_message
from src.services.faq import to_embedding_text
from src.services.knowledge import Outcome, Proposal, get_proposal, proposals_of_turn, resolve_proposal
from src.services.property import get_property
from src.utils.exceptions.property import PropertyNotFoundError

logger = logging.getLogger(__name__)

TITULOS = {
  "create": "Nueva pregunta frecuente",
  "update": "Editar pregunta frecuente",
  "delete": "Borrar pregunta frecuente",
  "category": "Nueva categoría",
}
TITULO_CONSULTA = "Propuesta de pregunta frecuente"
# interno: cambio pedido al «Agente Interno»; consulta: propuesta desde el hilo de una consulta (RF-07, RF-21).
BOTONES = { "interno": ("Confirmar", "Cancelar"), "consulta": ("Guardar", "Descartar") }
# Nota por defecto de una tarjeta ya resuelta, por ejemplo al volver a presionar un botón (RF-18).
ESTADOS = {
  "applied": "Este cambio ya se aplicó.",
  "discarded": "Esta propuesta se descartó: no se cambió nada.",
  "rejected": "Este cambio no se aplicó.",
}

# Nota del resultado de cada clic (RF-12 a RF-17).
APLICADO = {
  "create": "Listo: se guardó la pregunta frecuente.",
  "update": "Listo: se guardó la pregunta frecuente editada.",
  "delete": "Listo: se borró la pregunta frecuente.",
  "category": "Listo: se creó la categoría.",
}
NOTAS: dict[Outcome, str] = {
  "discarded": "Descartada: no se cambió nada.",
  "duplicate": "No se aplicó: ya existe con ese nombre o esa pregunta en la categoría.",
  "stale": "No se aplicó: la pregunta frecuente cambió o se borró desde que se propuso.",
}
SIN_EMBEDDING = "No se pudo preparar la búsqueda de la pregunta y no se aplicó nada. Intenta de nuevo."
NO_SE_PUDO = "No se pudo aplicar el cambio. Intenta de nuevo."
NO_EXISTE = "Esta propuesta ya no existe: no se cambió nada."
SOLO_AVISOS = "El conocimiento solo se administra desde el space de aviso: no se cambió nada."

def escaped(text: str | None) -> str:
  # Los widgets interpretan HTML: el texto de Marco o del LLM no debe dar formato ni romper la tarjeta (D13).
  return html.escape(text or "", quote = False).replace("\n", "<br>")

def field(label: str, text: str | None) -> dict[str, Any]:
  return { "decoratedText": { "topLabel": label, "text": escaped(text), "wrapText": True } }

def faq_section(header: str | None, question: str | None, answer: str | None) -> dict[str, Any]:
  section: dict[str, Any] = { "widgets": [field("Pregunta", question), field("Respuesta", answer)] }
  return { "header": header } | section if header else section

def button(text: str, function_url: str, proposal: Proposal, decision: str) -> dict[str, Any]:
  # La URL completa del endpoint: así lo exige un complemento HTTP (D6). El botón solo lleva el id (D1).
  parameters = [{ "key": "proposal", "value": proposal.id }, { "key": "decision", "value": decision }]
  return { "text": text, "onClick": { "action": { "function": function_url, "parameters": parameters } } }

def proposal_card(proposal: Proposal, function_url: str, note: str = "") -> dict[str, Any]:
  """Elemento de cardsV2 de una propuesta: muestra el cambio y, si sigue pendiente, sus botones."""
  sections: list[dict[str, Any]] = []
  if proposal.action == "category":
    sections.append({ "widgets": [{ "textParagraph": {
      "text": f"Se creará la categoría <b>{escaped(proposal.category)}</b> en {escaped(proposal.area)}."
    } }] })
  elif proposal.action == "delete":
    sections.append(faq_section("Se borrará", proposal.previous_question, proposal.previous_answer))
  elif proposal.action == "update":
    sections.append(faq_section("Actual", proposal.previous_question, proposal.previous_answer))
    sections.append(faq_section("Nueva", proposal.question, proposal.answer))
  else:
    sections.append(faq_section(None, proposal.question, proposal.answer))
  note = note or ESTADOS.get(proposal.status, "")
  if note:
    sections.append({ "widgets": [{ "textParagraph": { "text": escaped(note) } }] })
  if proposal.status == "pending":
    apply, discard = BOTONES.get(proposal.origin, BOTONES["interno"])
    sections.append({ "widgets": [{ "buttonList": { "buttons": [
      button(apply, function_url, proposal, "apply"), button(discard, function_url, proposal, "discard")
    ] } }] })
  title = TITULO_CONSULTA if proposal.origin == "consulta" else TITULOS.get(proposal.action, TITULOS["create"])
  return { "cardId": proposal.id, "card": {
    "header": { "title": title, "subtitle": f"{proposal.area} › {proposal.category}" }, "sections": sections
  } }

async def knowledge_allowed(session: AsyncSession, space: str | None) -> bool:
  """El conocimiento solo se ve y se cambia desde el space de aviso; sin la property, desde ninguno (RF-03, RF-04)."""
  try:
    notify = (await get_property(session, "whatsapp_notify_space")).strip()
  except PropertyNotFoundError:
    return False
  return bool(notify) and space == notify

async def turn_cards(session: AsyncSession, turn_id: str) -> list[dict[str, Any]]:
  """Tarjetas de las propuestas del turno, para la respuesta síncrona o diferida (D2, RF-11)."""
  proposals = await proposals_of_turn(session, turn_id)
  if not proposals:
    return []
  function_url = await get_property(session, "google_chat_audience")
  return [proposal_card(proposal, function_url) for proposal in proposals]

def note_of(proposal: Proposal, outcome: Outcome) -> str:
  if outcome == "applied":
    return APLICADO.get(proposal.action, APLICADO["create"])
  # Ya resuelta: la nota sale de su estado (RF-18).
  return NOTAS.get(outcome, "")

def replaced(message: ChatMessage | None, card: dict[str, Any]) -> dict[str, Any]:
  # El mensaje se reemplaza entero: se conservan su texto y las demás tarjetas, y cambia solo la del botón.
  cards = list(message.cards_v2) if message else []
  index = next((i for i, item in enumerate(cards) if item.get("cardId") == card["cardId"]), None)
  if index is None:
    cards.append(card)
  else:
    cards[index] = card
  return update_message(cards, message.text if message else None)

async def embedding_of(session: AsyncSession, proposal: Proposal) -> list[float]:
  # El mismo texto que la reindexación, para que la FAQ quede comparable con las demás (D7, D15).
  llm = await LLM.create(session)
  text = to_embedding_text(proposal.category, proposal.question or "", proposal.answer or "")
  return (await llm.embeddings.aembed_documents([text]))[0]

async def handle_click(session: AsyncSession, event: AddonEvent) -> dict[str, Any]:
  """Atiende un botón de una tarjeta de conocimiento y devuelve la tarjeta actualizada (RF-12 a RF-19)."""
  payload = event.chat.button_clicked_payload if event.chat else None
  parameters = event.common_event_object.parameters if event.common_event_object else {}
  proposal_id, decision = parameters.get("proposal", ""), parameters.get("decision", "")
  if payload is None or not proposal_id or decision not in ("apply", "discard"):
    return {}
  # Una tarjeta reenviada o un evento armado fuera del space de aviso no cambia nada (D5).
  if not await knowledge_allowed(session, payload.space.name if payload.space else None):
    return chat_reply(SOLO_AVISOS)
  function_url = await get_property(session, "google_chat_audience")
  current = None
  # Los errores solo dejan el id y el tipo en el log: la pregunta y la respuesta no (RF-27).
  try:
    current = await get_proposal(session, proposal_id)
    if current is None:
      return chat_reply(NO_EXISTE)
    embedding = None
    if decision == "apply" and current.status == "pending" and current.action in ("create", "update"):
      try:
        embedding = await embedding_of(session, current)
      except Exception as exc:
        # Sigue pendiente: los botones quedan para reintentar (RF-15).
        logger.error("No se pudo generar el embedding de la propuesta %s: %s", proposal_id, type(exc).__name__)
        return replaced(payload.message, proposal_card(current, function_url, SIN_EMBEDDING))
    resolution = await resolve_proposal(session, proposal_id, "apply" if decision == "apply" else "discard", embedding)
  except Exception as exc:
    logger.error("No se pudo resolver la propuesta de conocimiento %s: %s", proposal_id, type(exc).__name__)
    if current is None:
      return chat_reply(NO_SE_PUDO)
    return replaced(payload.message, proposal_card(current, function_url, NO_SE_PUDO))
  if resolution is None:
    return chat_reply(NO_EXISTE)
  card = proposal_card(resolution.proposal, function_url, note_of(resolution.proposal, resolution.outcome))
  return replaced(payload.message, card)
