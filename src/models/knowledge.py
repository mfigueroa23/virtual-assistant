from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from src.models.base import Base

class KnowledgeProposal(Base):
  __tablename__ = "knowledge_proposals"
  __table_args__ = (
    CheckConstraint("action IN ('create', 'update', 'delete', 'category')", name = "ck_knowledge_proposals_action"),
    CheckConstraint("status IN ('pending', 'applied', 'discarded', 'rejected')", name = "ck_knowledge_proposals_status"),
    CheckConstraint("origin IN ('interno', 'consulta')", name = "ck_knowledge_proposals_origin"),
  )

  # uuid4: viaja en los parámetros del botón y no se puede adivinar (D1, D6).
  id: Mapped[str] = mapped_column(String(36), primary_key = True)
  # Turno del «Agente Interno» que la propuso, para armar las tarjetas de su respuesta (D2); nulo si viene de una consulta.
  turn_id: Mapped[str | None] = mapped_column(String(64), index = True)
  # interno: pedida al «Agente Interno» (Confirmar/Cancelar); consulta: propuesta desde el hilo (Guardar/Descartar).
  origin: Mapped[str] = mapped_column(String(20))
  action: Mapped[str] = mapped_column(String(20))
  area_id: Mapped[int] = mapped_column(ForeignKey("areas.id", ondelete = "CASCADE"))
  # Nombre de la categoría: si no existe en el área se crea al aplicar.
  category: Mapped[str] = mapped_column(String(255))
  # FAQ que se edita o borra; queda nula si se borra antes de confirmar (RF-17).
  faq_id: Mapped[int | None] = mapped_column(ForeignKey("faqs.id", ondelete = "SET NULL"))
  question: Mapped[str | None] = mapped_column(Text)
  answer: Mapped[str | None] = mapped_column(Text)
  # Versión de la FAQ al proponer: se muestra en la tarjeta y detecta cambios antes de aplicar (RF-08, RF-17, D8).
  previous_question: Mapped[str | None] = mapped_column(Text)
  previous_answer: Mapped[str | None] = mapped_column(Text)
  # pending → applied | discarded | rejected (duplicada o cambió); solo pending acepta botones (RF-18).
  status: Mapped[str] = mapped_column(String(20), server_default = "pending")
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())
  resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
