from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from src.agents.context import AgentContext, CoordinatorContext
from src.agents.graph import knowledge_graph, sub_agent_graph
from src.services.knowledge import CONOCIMIENTO

@tool(parse_docstring=True)
async def delegar_area(area: str, consulta: str, runtime: ToolRuntime[CoordinatorContext]) -> str:
  """Deriva la consulta al especialista de un área y devuelve su respuesta.

  Args:
      area: Nombre exacto de una de las áreas disponibles.
      consulta: La consulta del usuario, completa y autocontenida, incluyendo el contexto necesario de la conversación.
  """
  context = runtime.context
  sub_agent = context.sub_agents.get(area)
  if sub_agent is None:
    return f"El área '{area}' no existe. Áreas disponibles: {', '.join(context.sub_agents)}."
  # Solo el área interno «Conocimiento» usa el grafo con las tools de escritura (D3, D4 del plan 009).
  is_knowledge = sub_agent.audiencia == "interno" and sub_agent.area == CONOCIMIENTO
  result = await (knowledge_graph if is_knowledge else sub_agent_graph).ainvoke(
    { "messages": [HumanMessage(consulta)] },
    context = AgentContext(
      llm = context.llm,
      system_prompt = sub_agent.prompt,
      embeddings = context.embeddings,
      area = sub_agent.area,
      audiencia = sub_agent.audiencia,
      turn_id = context.turn_id
    )
  )
  return result["messages"][-1].text
