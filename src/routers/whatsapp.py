import logging
import time
import psycopg
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.session import SessionDep
from src.interfaces.whatsapp import WhatsappEvent
from src.services.property import get_property
from src.services.whatsapp_reply import handle_event
from src.services.ycloud import verify_signature
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError
from src.utils.exceptions.whatsapp import InvalidSignatureError

router = APIRouter(prefix="/api/v1", tags=["WhatsApp"])
logger = logging.getLogger(__name__)

NO_DISPONIBLE = "Servicio no disponible. Intenta de nuevo más tarde"

async def channel_enabled(session: AsyncSession) -> bool:
  # Si falta la property el canal queda apagado: hay que habilitarlo a propósito.
  try:
    return (await get_property(session, "whatsapp_enabled")).strip().lower() == "true"
  except PropertyNotFoundError:
    return False

@router.post(
  "/whatsapp/events",
  responses={ 401: { "description": "Firma de YCloud no válida" }, 503: { "description": "Servicio no disponible" } }
)
async def whatsapp_events(request: Request, session: SessionDep) -> Response:
  # Se firma el cuerpo crudo: se lee antes de parsear para no alterar espacios ni el orden de las claves (D8).
  body = await request.body()
  try:
    secret = await get_property(session, "ycloud_webhook_secret")
    verify_signature(request.headers.get("YCloud-Signature"), body, secret, time.time())
    if not await channel_enabled(session):
      return Response(status_code = status.HTTP_200_OK)
    try:
      event = WhatsappEvent.model_validate_json(body)
    except ValidationError:
      # Un cuerpo firmado pero inesperado no se arregla con reintentos: se ignora.
      logger.warning("Evento de YCloud con un formato no reconocido, se ignora")
      return Response(status_code = status.HTTP_200_OK)
    # Solo guarda y agenda: la respuesta del asistente se envía después, en segundo plano.
    await handle_event(request.app.state.whatsapp_graph, session, event)
    return Response(status_code = status.HTTP_200_OK)
  except InvalidSignatureError as exc:
    logger.warning("Evento de YCloud rechazado: %s", exc)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma de YCloud no válida") from exc
  # Con 503 YCloud reintenta el evento hasta 7 veces, así un corte breve no pierde mensajes (D9).
  except PropertyNotFoundError as exc:
    logger.error("WhatsApp mal configurado: %s", exc)
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_DISPONIBLE) from exc
  except (DatabaseUnavailableError, psycopg.OperationalError) as exc:
    logger.error("Base de datos no disponible: %s", type(exc).__name__)
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_DISPONIBLE) from exc
