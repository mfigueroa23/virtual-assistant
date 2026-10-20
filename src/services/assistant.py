from collections.abc import Sequence
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph import MessagesState
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from src.agents.context import CoordinatorContext
from src.agents.llm import LLM
from src.services.agents import get_prompt, list_sub_agents
from src.utils.exceptions.agents import AgentNotFoundError

async def ask(
  graph: CompiledStateGraph[MessagesState, CoordinatorContext],
  session: AsyncSession,
  coordinator: str,
  audiencia: str,
  thread_id: str,
  mensaje: str,
  previous: Sequence[AnyMessage] = ()
) -> str:
  llm = await LLM.create(session)
  prompt = await get_prompt(session, coordinator)
  sub_agents = await list_sub_agents(session, audiencia = audiencia)
  if not sub_agents:
    raise AgentNotFoundError(f"sub-agentes de la audiencia '{audiencia}'")
  result = await graph.ainvoke(
    # previous son mensajes ya ocurridos que aún no están en el historial, como los de WhatsApp durante la pausa (D4).
    { "messages": [*previous, HumanMessage(mensaje)] },
    config = { "configurable": { "thread_id": thread_id } },
    context = CoordinatorContext(
      llm = llm.llm,
      system_prompt = prompt,
      embeddings = llm.embeddings,
      audiencia = audiencia,
      sub_agents = { sub_agent.area: sub_agent for sub_agent in sub_agents }
    )
  )
  return result["messages"][-1].text
