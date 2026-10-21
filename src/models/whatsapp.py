from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from src.models.base import Base

class WhatsappConversation(Base):
  __tablename__ = "whatsapp_conversations"

  # Número del contacto en E.164, tal como lo envía YCloud.
  phone: Mapped[str] = mapped_column(String(32), primary_key = True)
  # Número del negocio al que escribió el contacto: es el "from" de las respuestas (D14).
  business_phone: Mapped[str] = mapped_column(String(32))
  # Hasta cuándo el asistente no responde porque Marco tomó la conversación.
  paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
  # Cuándo vence la espera de agrupación del lote en curso.
  batch_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
  # Quién procesa un lote: evita dos respuestas a la vez y se libera sola si queda vieja (D3).
  processing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
  # Sube con cada mensaje de Marco: si cambió durante el lote, la respuesta se descarta (D6).
  epoch: Mapped[int] = mapped_column(Integer, server_default = "0")
  # Nombre de perfil de WhatsApp del contacto, para el aviso a Marco; se actualiza con cada mensaje entrante.
  contact_name: Mapped[str | None] = mapped_column(String(255))
  # Último mensaje del contacto: WhatsApp solo permite texto libre por la API hasta 24 h después (RF-19).
  last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))

class WhatsappMessage(Base):
  __tablename__ = "whatsapp_messages"
  __table_args__ = (
    CheckConstraint("direction IN ('inbound', 'echo')", name = "ck_whatsapp_messages_direction"),
    CheckConstraint("status IN ('pending', 'processing', 'history')", name = "ck_whatsapp_messages_status"),
  )

  # Id de YCloud del mensaje (entrante o echo).
  id: Mapped[str] = mapped_column(String(64), primary_key = True)
  phone: Mapped[str] = mapped_column(ForeignKey("whatsapp_conversations.phone", ondelete = "CASCADE"), index = True)
  # inbound (contacto) o echo (Marco desde la app).
  direction: Mapped[str] = mapped_column(String(10))
  # Es una cola: pending → processing → se borra al quedar en el historial; history espera al próximo lote (D4).
  status: Mapped[str] = mapped_column(String(20))
  sent_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
  type: Mapped[str] = mapped_column(String(20))
  # Texto, leyenda o línea descriptiva de los tipos que no se leen.
  text: Mapped[str | None] = mapped_column(Text)
  media_link: Mapped[str | None] = mapped_column(Text)
  media_mime: Mapped[str | None] = mapped_column(String(255))
  media_name: Mapped[str | None] = mapped_column(String(255))

class WhatsappNotice(Base):
  __tablename__ = "whatsapp_notices"

  # Hilo de Google Chat del aviso (spaces/…/threads/…): una respuesta en él va al contacto (D1, D3).
  thread: Mapped[str] = mapped_column(String(255), primary_key = True)
  phone: Mapped[str] = mapped_column(ForeignKey("whatsapp_conversations.phone", ondelete = "CASCADE"), index = True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

class WhatsappEvent(Base):
  __tablename__ = "whatsapp_events"

  # Id del evento de YCloud, para descartar los reintentos.
  id: Mapped[str] = mapped_column(String(64), primary_key = True)
  received_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now(), index = True)
