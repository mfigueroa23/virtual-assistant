import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any
import httpx
from google.auth import crypt, exceptions, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from src.interfaces.google_chat import AddonEvent, message_body
from src.services.attachments import MAX_BYTES, Attachment
from src.services.property import get_property
from src.utils.exceptions.attachments import AttachmentTooLargeError
from src.utils.exceptions.google_chat import InvalidChatNameError, InvalidGoogleTokenError

logger = logging.getLogger(__name__)

# El complemento de Google Workspace firma cada evento con un ID token de Google.
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_ISSUERS = { "accounts.google.com", "https://accounts.google.com" }
# Los archivos subidos al chat se descargan con la cuenta de servicio de la app de Chat.
CHAT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
CHAT_MEDIA_URL = "https://chat.googleapis.com/v1/media"
CHAT_API_URL = "https://chat.googleapis.com/v1"
# Los nombres del evento o de la configuración van en la ruta de la URL junto al token de la cuenta de servicio: solo se
# aceptan los formatos de Google Chat, sin rutas relativas, consultas ni escapes (D8 del plan 013).
SPACE_NAME = re.compile(r"spaces/[A-Za-z0-9_-]+")
_SEGMENT = r"(?!\.\.?(?:/|$))[A-Za-z0-9_\-+=~.]+"
ATTACHMENT_NAME = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*")

# Caché de los certificados públicos de Google respetando el max-age que indica Google.
_certs: dict[str, str] = {}
_certs_expire_at = 0.0

def valid_space(name: str) -> bool:
  return SPACE_NAME.fullmatch(name) is not None

def valid_attachment_name(name: str) -> bool:
  return ATTACHMENT_NAME.fullmatch(name) is not None

async def get_google_certs(http: httpx.AsyncClient) -> dict[str, str]:
  global _certs, _certs_expire_at
  if time.monotonic() >= _certs_expire_at:
    response = await http.get(GOOGLE_CERTS_URL)
    response.raise_for_status()
    _certs = response.json()
    max_age = re.search(r"max-age=(\d+)", response.headers.get("Cache-Control", ""))
    _certs_expire_at = time.monotonic() + (int(max_age.group(1)) if max_age else 0)
  return _certs

async def verify_addon_token(token: str | None, audience: str, service_account: str, http: httpx.AsyncClient) -> None:
  if not token:
    raise InvalidGoogleTokenError("Falta el token Bearer")
  try:
    claims = jwt.decode(token, certs = await get_google_certs(http), audience = audience)
  except (ValueError, exceptions.GoogleAuthError, httpx.HTTPError) as exc:
    raise InvalidGoogleTokenError(str(exc)) from exc
  if claims.get("iss") not in GOOGLE_ISSUERS:
    raise InvalidGoogleTokenError("El emisor del token no es Google")
  # Solo se acepta la cuenta de servicio de este complemento, no cualquier ID token de Google.
  if claims.get("email") != service_account or claims.get("email_verified") is not True:
    raise InvalidGoogleTokenError("El token no es de la cuenta de servicio del complemento")

def conversation_key(event: AddonEvent) -> str:
  # En un mensaje directo cada mensaje abre un hilo nuevo, la conversación es el space. En un space es el hilo.
  payload = event.chat.message_payload if event.chat else None
  space = payload.space if payload else None
  if space is not None and space.space_type == "DIRECT_MESSAGE":
    return space.name
  thread = payload.message.thread if payload and payload.message else None
  return thread.name if thread else (space.name if space else "")

def message_text(event: AddonEvent) -> str:
  payload = event.chat.message_payload if event.chat else None
  if payload is None or payload.message is None:
    return ""
  is_dm = payload.space is not None and payload.space.space_type == "DIRECT_MESSAGE"
  # En un space solo cuenta el texto que acompaña a la mención, en un mensaje directo el texto completo.
  text = payload.message.text if is_dm else payload.message.argument_text
  return (text or "").strip()

def mention_of(event: AddonEvent) -> str:
  # En un space el mensaje diferido menciona al remitente; en un mensaje directo o sin remitente no hace falta.
  payload = event.chat.message_payload if event.chat else None
  if payload is None or payload.message is None or payload.message.sender is None:
    return ""
  if payload.space is not None and payload.space.space_type == "DIRECT_MESSAGE":
    return ""
  return f"<{payload.message.sender.name}>"

def attachments_of(event: AddonEvent) -> list[Attachment]:
  payload = event.chat.message_payload if event.chat else None
  if payload is None or payload.message is None:
    return []
  # Solo se descargan los archivos subidos al chat, un enlace de Drive queda sin resource_name.
  return [
    Attachment(item.content_name, item.content_type,
               item.attachment_data_ref.resource_name if item.source != "DRIVE_FILE" and item.attachment_data_ref else None)
    for item in payload.message.attachment
  ]

async def service_account_token(service_account: dict[str, Any], http: httpx.AsyncClient) -> str:
  # Flujo JWT-bearer con httpx: el transporte de google.auth es síncrono y bloquearía el servidor.
  signer = crypt.RSASigner.from_service_account_info(service_account)
  token_uri = service_account["token_uri"]
  now = int(time.time())
  assertion = jwt.encode(signer, {
    "iss": service_account["client_email"], "scope": CHAT_SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600
  })
  response = await http.post(token_uri, data = {
    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion.decode()
  })
  response.raise_for_status()
  return response.json()["access_token"]

async def chat_token(session: AsyncSession, http: httpx.AsyncClient) -> str | None:
  # Sin la cuenta de servicio no se puede descargar ni publicar en Chat; quien llama decide cómo seguir.
  try:
    service_account = json.loads(await get_property(session, "google_chat_service_account_json"))
    return await service_account_token(service_account, http)
  except Exception as exc:
    logger.error("No se pudo obtener el token de la cuenta de servicio de Google Chat: %s", type(exc).__name__)
    return None

async def download(resource_name: str, token: str, http: httpx.AsyncClient) -> bytes:
  # Chat no informa el tamaño del archivo: se corta la descarga al pasar el máximo en vez de cargarlo entero.
  if not valid_attachment_name(resource_name):
    raise InvalidChatNameError("adjunto")
  chunks: list[bytes] = []
  size = 0
  headers = { "Authorization": f"Bearer {token}" }
  async with http.stream("GET", f"{CHAT_MEDIA_URL}/{resource_name}", params = { "alt": "media" }, headers = headers) as response:
    response.raise_for_status()
    async for chunk in response.aiter_bytes():
      size += len(chunk)
      if size > MAX_BYTES:
        raise AttachmentTooLargeError(resource_name)
      chunks.append(chunk)
  return b"".join(chunks)

async def create_message(
  space: str, text: str, token: str, http: httpx.AsyncClient, cards: Sequence[dict[str, Any]] = (), thread: str | None = None
) -> str | None:
  """Publica el mensaje y devuelve el hilo que abrió, o None si la respuesta no lo trae."""
  if not valid_space(space):
    raise InvalidChatNameError("space")
  # Sin "thread" Google Chat publica el mensaje fuera del hilo original.
  headers = { "Authorization": f"Bearer {token}" }
  body = message_body(text, cards)
  params: dict[str, str] | None = None
  if thread:
    # En el hilo del aviso; si ya no existe, Google Chat abre uno nuevo en vez de rechazar el mensaje (spec 009).
    body["thread"] = { "name": thread }
    params = { "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" }
  response = await http.post(f"{CHAT_API_URL}/{space}/messages", json = body, params = params, headers = headers)
  response.raise_for_status()
  created = response.json().get("thread") or {}
  return created.get("name")
