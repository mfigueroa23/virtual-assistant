from typing import Literal
from langchain.tools import ToolRuntime, tool
from src.agents.context import AgentContext
from src.database.session import SessionLocal
from src.services.knowledge import Action, create_proposal, list_knowledge, search_knowledge

ACCIONES: dict[str, Action] = { "crear": "create", "editar": "update", "borrar": "delete", "crear_categoria": "category" }
# La tool no escribe: Marco confirma con el botón de la tarjeta (RF-07). El modelo no debe dar el cambio por hecho.
GUIA_PROPUESTA = (
  "Marco verá una tarjeta con el cambio para confirmarlo o cancelarlo. Dile en una frase qué propusiste. "
  "No digas que el cambio está hecho."
)
SIN_TURNO = "No se pueden proponer cambios en esta conversación."

@tool(parse_docstring=True)
async def ver_conocimiento(runtime: ToolRuntime[AgentContext]) -> str:
  """Lista las áreas del conocimiento con sus categorías y cuántas preguntas frecuentes tiene cada una."""
  async with SessionLocal() as session:
    areas = await list_knowledge(session)
  if not areas:
    return "No hay áreas de conocimiento cargadas."
  lines: list[str] = []
  for area in areas:
    lines.append(f"- {area.name}: {area.description}")
    lines.extend(f"  - {category.name} ({category.faqs})" for category in area.categories)
    if not area.categories:
      lines.append("  - (sin categorías)")
  return "\n".join(lines)

@tool(parse_docstring=True)
async def buscar_conocimiento(consulta: str, runtime: ToolRuntime[AgentContext]) -> str:
  """Busca preguntas frecuentes del conocimiento por similitud. Devuelve su id, que se usa para editarlas o borrarlas.

  Args:
      consulta: El tema o la pregunta a buscar, en español.
  """
  vector = await runtime.context.embeddings.aembed_query(consulta)
  async with SessionLocal() as session:
    hits = await search_knowledge(session, vector)
  if not hits:
    return "No se encontraron preguntas frecuentes relacionadas."
  return "\n\n---\n\n".join(
    f"[id {hit.id}] {hit.area} › {hit.category}\nPregunta: {hit.question}\nRespuesta: {hit.answer}" for hit in hits
  )

@tool(parse_docstring=True)
async def proponer_cambio(
  accion: Literal["crear", "editar", "borrar", "crear_categoria"],
  runtime: ToolRuntime[AgentContext],
  area: str = "",
  categoria: str = "",
  pregunta: str = "",
  respuesta: str = "",
  faq_id: int | None = None
) -> str:
  """Propone un cambio en el conocimiento. Nada se escribe hasta que Marco lo confirme en la tarjeta.

  Úsala para cada cambio que pida Marco. Para editar o borrar, busca antes la pregunta con buscar_conocimiento y usa
  su id; editar cambia solo la pregunta y la respuesta, y lo que no indiques se mantiene.

  Args:
      accion: crear (una pregunta frecuente), editar, borrar o crear_categoria.
      area: Para crear o crear_categoria, el nombre exacto de un área de ver_conocimiento.
      categoria: Para crear o crear_categoria, la categoría; si no existe en el área, se crea al confirmar.
      pregunta: Para crear o editar, la pregunta completa y autocontenida.
      respuesta: Para crear o editar, la respuesta completa, sin datos personales de contactos.
      faq_id: Para editar o borrar, el id de la pregunta frecuente según buscar_conocimiento.
  """
  context = runtime.context
  # Sin turno la propuesta no tendría tarjeta y Marco no podría confirmarla (D2).
  if not context.turn_id:
    return SIN_TURNO
  action = ACCIONES[accion]
  if action in ("update", "delete") and faq_id is None:
    return "Para editar o borrar indica el id de la pregunta frecuente: búscala con buscar_conocimiento."
  async with SessionLocal() as session:
    result = await create_proposal(
      session, "interno", action, area, categoria, pregunta, respuesta, faq_id, context.turn_id
    )
  return result if isinstance(result, str) else GUIA_PROPUESTA

KNOWLEDGE_TOOLS = [ver_conocimiento, buscar_conocimiento, proponer_cambio]
