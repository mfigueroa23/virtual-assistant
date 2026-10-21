import logging
from typing import Annotated, Any
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.session import SessionDep
from src.interfaces.google_chat import AddonEvent, chat_reply
from src.services.google_chat import (
  attachments_of, conversation_key, message_text, verify_addon_token
)
from src.services.google_chat_reply import NO_DISPONIBLE, answer, read_thresholds
from src.services.http import outbound_client
from src.services.knowledge_card import handle_click
from src.services.property import get_property
from src.services.whatsapp_relay import relay_reply
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.google_chat import InvalidGoogleTokenError
from src.utils.exceptions.property import PropertyNotFoundError

router = APIRouter(prefix="/api/v1", tags=["Google Chat"])
logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)

async def verify_token(session: AsyncSession, credentials: HTTPAuthorizationCredentials | None) -> None:
  # Sin token se rechaza antes de leer la configuración.
  if credentials is None:
    raise InvalidGoogleTokenError("Falta el token Bearer")
  # Sin caché: un cambio en las properties se aplica desde el siguiente evento.
  audience = await get_property(session, "google_chat_audience")
  service_account = await get_property(session, "google_chat_addon_service_account")
  async with outbound_client(10) as http:
    await verify_addon_token(credentials.credentials, audience, service_account, http)

@router.post("/google-chat/events", responses={ 401: { "description": "Token de Google no válido" } })
async def google_chat_events(
  request: Request,
  event: AddonEvent,
  session: SessionDep,
  credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> dict[str, Any]:
  try:
    await verify_token(session, credentials)
    if event.chat is not None and event.chat.added_to_space_payload is not None:
      return chat_reply(await get_property(session, "google_chat_welcome"))
    # Un clic en una tarjeta de conocimiento no es un mensaje: no va al relay ni al «Agente Interno» (spec 009).
    if event.chat is not None and event.chat.button_clicked_payload is not None:
      return await handle_click(session, event)
    if not conversation_key(event) or not (message_text(event) or attachments_of(event)):
      return {}
    # Una respuesta en el hilo de un aviso de WhatsApp va al contacto; el resto sigue al Agente Interno (D9 del plan 008).
    relayed = await relay_reply(request.app.state.whatsapp_graph, session, event)
    if relayed is not None:
      return chat_reply(relayed)
    notice, maximum = await read_thresholds(session)
    # answer decide entre responder en el hilo o avisar y publicar después un mensaje nuevo.
    return await answer(request.app.state.graph, event, notice, maximum)
  except InvalidGoogleTokenError as exc:
    logger.warning("Evento de Google Chat rechazado: %s", exc)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de Google no válido") from exc
  # Google Chat solo muestra la respuesta si es 200, por eso los errores se responden como mensaje.
  except PropertyNotFoundError as exc:
    logger.error("Google Chat mal configurado: %s", exc)
    return chat_reply(NO_DISPONIBLE)
  except (DatabaseUnavailableError, psycopg.OperationalError) as exc:
    logger.error("Base de datos no disponible: %s", exc)
    return chat_reply(NO_DISPONIBLE)
  except Exception:
    logger.exception("Ha ocurrido un error al responder en Google Chat")
    return chat_reply(NO_DISPONIBLE)
