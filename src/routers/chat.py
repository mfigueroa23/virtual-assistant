import logging
import psycopg
from fastapi import APIRouter, Request, Response, status
from src.database.session import SessionDep
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError
from src.utils.exceptions.agents import AgentNotFoundError
from src.interfaces.chat import ChatRequest, ChatResponse
from src.services.assistant import ask

route = APIRouter(tags=['Chat'])
logger = logging.getLogger(__name__)

AUDIENCIAS = ("externo",)
COORDINADOR = "Agente Externo"

@route.post("/chat", responses={ 500: { "model": ChatResponse }, 503: { "model": ChatResponse } })
async def chat(request: Request, response: Response, message: ChatRequest, session: SessionDep) -> ChatResponse:
  try:
    respuesta = await ask(request.app.state.graph, session, COORDINADOR, AUDIENCIAS, str(message.chat_id), message.mensaje)
    return ChatResponse(respuesta = respuesta)
  except (PropertyNotFoundError, AgentNotFoundError) as exc:
    logger.error("Agente mal configurado: %s", exc)
    response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return ChatResponse(respuesta="Servicio no disponible. Intente de nuevo más tarde")
  except (DatabaseUnavailableError, psycopg.OperationalError) as exc:
    logger.error("Base de datos no disponible: %s", exc)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ChatResponse(respuesta="Servicio no disponible. Intente de nuevo más tarde")
  except Exception:
    logger.exception("Ha ocurrido un error al solicitar una respuesta")
    response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return ChatResponse(respuesta="Servicio no disponible. Intente de nuevo más tarde")
