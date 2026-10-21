from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

class YCloudModel(BaseModel):
  # YCloud usa camelCase en los objetos propios y deja los campos extra fuera del modelo.
  model_config = ConfigDict(alias_generator = to_camel, populate_by_name = True, extra = "ignore")

class Media(BaseModel):
  # Los objetos de medios vienen de WhatsApp tal cual, en snake_case.
  model_config = ConfigDict(extra = "ignore")

  id: str | None = None
  # Se descarga con X-API-Key durante 30 días.
  link: str | None = None
  caption: str | None = None
  filename: str | None = None
  mime_type: str | None = None

class TextBody(BaseModel):
  body: str = ""

class Reaction(BaseModel):
  message_id: str | None = None
  # Falta cuando el contacto quita la reacción.
  emoji: str | None = None

class MessageContent(YCloudModel):
  id: str
  from_: str = Field("", alias = "from")
  to: str = ""
  type: str = ""
  send_time: datetime | None = None
  text: TextBody | None = None
  image: Media | None = None
  audio: Media | None = None
  document: Media | None = None
  video: Media | None = None
  sticker: Media | None = None
  location: dict[str, Any] | None = None
  contacts: list[dict[str, Any]] | None = None
  reaction: Reaction | None = None

class CustomerProfile(BaseModel):
  # Nombre de perfil que el contacto puso en WhatsApp; puede faltar.
  name: str | None = None

class InboundMessage(MessageContent):
  # Solo viene en los mensajes de grupo, que no se atienden.
  group_id: str | None = None
  customer_profile: CustomerProfile | None = None

class WhatsappMessage(MessageContent):
  """Mensaje enviado por el negocio: un echo desde la app o una actualización de estado."""
  status: str | None = None
  error_code: str | None = None
  error_message: str | None = None
  create_time: datetime | None = None

class WhatsappEvent(YCloudModel):
  """Evento del webhook de YCloud, solo con los campos que se usan."""
  id: str
  type: str
  whatsapp_inbound_message: InboundMessage | None = None
  whatsapp_message: WhatsappMessage | None = None
