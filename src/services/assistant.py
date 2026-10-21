from collections.abc import Collection, Sequence
from uuid import uuid4
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph import MessagesState
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from src.agents.context import CoordinatorContext
from src.agents.llm import LLM
from src.services.agents import get_prompt, list_sub_agents
from src.utils.exceptions.agents import AgentNotFoundError

async def run_turn(
  graph: CompiledStateGraph[MessagesState, CoordinatorContext],
  session: AsyncSession,
  coordinator: str,
  audiencia: str,
  thread_id: str,
  mensaje: str,
  previous: Sequence[AnyMessage] = (),
  instructions: str = "",
  turn_id: str | None = None,
  exclude: Collection[str] = ()
) -> list[AnyMessage]:
  """Ejecuta un turno y devuelve solo sus mensajes: llamadas a tools, sus resultados y la respuesta final (D3)."""
  llm = await LLM.create(session)
  prompt = await get_prompt(session, coordinator)
  sub_agents = await list_sub_agents(session, audiencia = audiencia)
  if not sub_agents:
    raise AgentNotFoundError(f"sub-agentes de la audiencia '{audiencia}'")
  # Las áreas excluidas no se ofrecen; el turno sigue aunque no quede ninguna (D5 del plan 009).
  available = [sub_agent for sub_agent in sub_agents if sub_agent.area not in exclude]
  # Un id propio marca dónde empieza el turno dentro del historial completo que devuelve el grafo.
  turn = HumanMessage(mensaje, id = turn_id or str(uuid4()))
  result = await graph.ainvoke(
    # previous son mensajes ya ocurridos que aún no están en el historial, como los de WhatsApp durante la pausa (D4).
    { "messages": [*previous, turn] },
    config = { "configurable": { "thread_id": thread_id } },
    context = CoordinatorContext(
      llm = llm.llm,
      system_prompt = prompt,
      embeddings = llm.embeddings,
      audiencia = audiencia,
      sub_agents = { sub_agent.area: sub_agent for sub_agent in available },
      instructions = instructions,
      turn_id = str(turn.id)
    )
  )
  messages: list[AnyMessage] = result["messages"]
  start = [message.id for message in messages].index(turn.id)
  return messages[start + 1:]

async def ask(
  graph: CompiledStateGraph[MessagesState, CoordinatorContext],
  session: AsyncSession,
  coordinator: str,
  audiencia: str,
  thread_id: str,
  mensaje: str,
  previous: Sequence[AnyMessage] = ()
) -> str:
  turn = await run_turn(graph, session, coordinator, audiencia, thread_id, mensaje, previous)
  return turn[-1].text
