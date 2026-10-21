from collections.abc import Sequence
from typing import Any
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class ChatModel(BaseModel):
  model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class ChatThread(ChatModel):
  name: str

class AttachmentDataRef(ChatModel):
  resource_name: str

class ChatAttachment(ChatModel):
  content_name: str = "archivo"
  content_type: str = ""
  # UPLOADED_CONTENT si se subió al chat (trae attachmentDataRef) o DRIVE_FILE si es un enlace de Drive.
  source: str | None = None
  attachment_data_ref: AttachmentDataRef | None = None

class ChatUser(ChatModel):
  # users/{id}: se usa para mencionar al remitente con <users/{id}>.
  name: str
  # HUMAN o BOT.
  type: str | None = None

class ChatMessage(ChatModel):
  text: str | None = None
  sender: ChatUser | None = None
  # En un space, el texto sin la mención al asistente.
  argument_text: str | None = None
  thread: ChatThread | None = None
  attachment: list[ChatAttachment] = []
  # Tarjetas del mensaje: al presionar un botón se reemplaza solo la tarjeta del botón (spec 009).
  cards_v2: list[dict[str, Any]] = []

class ChatSpace(ChatModel):
  name: str
  space_type: str | None = None

class MessagePayload(ChatModel):
  message: ChatMessage | None = None
  space: ChatSpace | None = None

class SpacePayload(ChatModel):
  space: ChatSpace | None = None

class ButtonPayload(ChatModel):
  # El mensaje con la tarjeta presionada y su space.
  message: ChatMessage | None = None
  space: ChatSpace | None = None

class AddonChat(ChatModel):
  message_payload: MessagePayload | None = None
  added_to_space_payload: SpacePayload | None = None
  button_clicked_payload: ButtonPayload | None = None

class CommonEventObject(ChatModel):
  # Parámetros del botón presionado (onClick.action.parameters).
  parameters: dict[str, str] = {}

class AddonEvent(ChatModel):
  """Evento de Google Chat enviado por el complemento de Google Workspace, solo con los campos que se usan."""
  chat: AddonChat | None = None
  common_event_object: CommonEventObject | None = None

def message_body(text: str | None, cards: Sequence[dict[str, Any]]) -> dict[str, Any]:
  body: dict[str, Any] = { "text": text } if text else {}
  return body | ({ "cardsV2": list(cards) } if cards else {})

def chat_reply(text: str, cards: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
  # Respuesta síncrona del complemento: Google Chat la publica en el hilo del mensaje.
  return { "hostAppDataAction": { "chatDataAction": { "createMessageAction": { "message": message_body(text, cards) } } } }

def update_message(cards: Sequence[dict[str, Any]], text: str | None = None) -> dict[str, Any]:
  # Respuesta a un clic: reemplaza el mensaje de la tarjeta, así que lleva de nuevo su texto y todas sus tarjetas.
  return { "hostAppDataAction": { "chatDataAction": { "updateMessageAction": { "message": message_body(text, cards) } } } }
