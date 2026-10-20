import hashlib
import hmac
import httpx
from src.services.attachments import MAX_BYTES
from src.utils.exceptions.attachments import AttachmentTooLargeError
from src.utils.exceptions.whatsapp import InvalidSignatureError

YCLOUD_API_URL = "https://api.ycloud.com/v2"
# Diferencia máxima entre la marca de tiempo de la firma y el reloj del servidor.
SIGNATURE_TOLERANCE = 300
# Máximo de text.body en un mensaje de texto de WhatsApp.
TEXT_LIMIT = 4096

def verify_signature(header: str | None, body: bytes, secret: str, now: float) -> None:
  # La firma es HMAC-SHA256 de "{t}." + el cuerpo crudo: se verifica antes de parsear el JSON (D8).
  if not header:
    raise InvalidSignatureError("Falta la cabecera YCloud-Signature")
  fields = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
  timestamp, received = fields.get("t", ""), fields.get("s", "")
  if not timestamp.isdigit() or not received:
    raise InvalidSignatureError("La cabecera YCloud-Signature está mal formada")
  if abs(now - int(timestamp)) > SIGNATURE_TOLERANCE:
    raise InvalidSignatureError("La firma de YCloud está vencida")
  expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
  if not hmac.compare_digest(expected, received):
    raise InvalidSignatureError("La firma de YCloud no coincide")

async def send_text(api_key: str, from_: str, to: str, body: str, http: httpx.AsyncClient) -> None:
  response = await http.post(
    f"{YCLOUD_API_URL}/whatsapp/messages/sendDirectly",
    json = { "from": from_, "to": to, "type": "text", "text": { "body": body } },
    headers = { "X-API-Key": api_key }
  )
  response.raise_for_status()

async def mark_as_read(api_key: str, inbound_id: str, http: httpx.AsyncClient) -> None:
  # Marca también como leídos los mensajes anteriores de la conversación.
  response = await http.post(
    f"{YCLOUD_API_URL}/whatsapp/inboundMessages/{inbound_id}/markAsRead", headers = { "X-API-Key": api_key }
  )
  response.raise_for_status()

async def download_media(api_key: str, link: str, http: httpx.AsyncClient) -> bytes:
  # Igual que la descarga de Google Chat: se corta al pasar el máximo en vez de cargar el archivo entero.
  chunks: list[bytes] = []
  size = 0
  async with http.stream("GET", link, headers = { "X-API-Key": api_key }) as response:
    response.raise_for_status()
    async for chunk in response.aiter_bytes():
      size += len(chunk)
      if size > MAX_BYTES:
        raise AttachmentTooLargeError(link)
      chunks.append(chunk)
  return b"".join(chunks)

def split_words(paragraph: str, limit: int) -> list[str]:
  # Un párrafo más largo que el límite se corta entre palabras; solo una palabra más larga que el límite se parte.
  pieces: list[str] = []
  current = ""
  for word in paragraph.split(" "):
    while len(word) > limit:
      if current:
        pieces.append(current)
        current = ""
      pieces.append(word[:limit])
      word = word[limit:]
    candidate = f"{current} {word}" if current else word
    if len(candidate) <= limit:
      current = candidate
    else:
      pieces.append(current)
      current = word
  if current:
    pieces.append(current)
  return pieces

def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
  """Divide la respuesta en mensajes de hasta `limit` caracteres, primero por párrafos y luego por palabras (D15)."""
  if len(text) <= limit:
    return [text]
  parts: list[str] = []
  current = ""
  for paragraph in text.split("\n\n"):
    if len(paragraph) > limit:
      if current:
        parts.append(current)
      *complete, current = split_words(paragraph, limit)
      parts += complete
      continue
    candidate = f"{current}\n\n{paragraph}" if current else paragraph
    if len(candidate) <= limit:
      current = candidate
    else:
      parts.append(current)
      current = paragraph
  if current:
    parts.append(current)
  return parts
