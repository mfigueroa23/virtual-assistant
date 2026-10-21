from collections.abc import Sequence
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.runtime import Runtime
from src.agents.context import CoordinatorContext
from src.agents.tools.delegate import delegar_area
from src.agents.tools.handoff import consultar_a_marco, derivar_a_marco, registrar_encargo

TOOLS = [delegar_area]
# Solo WhatsApp puede derivar, registrar encargos y consultar a Marco: el chat web y Google Chat no ven estas acciones (D1).
WHATSAPP_TOOLS = [delegar_area, derivar_a_marco, registrar_encargo, consultar_a_marco]

def build_prompt(context: CoordinatorContext) -> str:
  areas = "\n".join(f"- {sub_agent.area}: {sub_agent.description}" for sub_agent in context.sub_agents.values())
  prompt = f"{context.system_prompt}\n\nÁreas disponibles:\n{areas}"
  # Las instrucciones del turno (encargos aceptables, trato familiar) se leen en cada turno (D14).
  return f"{prompt}\n\n{context.instructions}" if context.instructions else prompt

def build_coordinator(tools: Sequence[BaseTool]) -> StateGraph[MessagesState, CoordinatorContext]:
  async def coordinator(state: MessagesState, runtime: Runtime[CoordinatorContext]) -> dict:
    context = runtime.context
    llm = context.llm.bind_tools(tools)
    response = await llm.ainvoke([SystemMessage(build_prompt(context)), *state["messages"]])
    return { "messages": [response] }

  builder = StateGraph(MessagesState, context_schema=CoordinatorContext)
  builder.add_node("coordinator", coordinator)
  builder.add_node("tools", ToolNode(tools))
  builder.add_edge(START, "coordinator")
  builder.add_conditional_edges("coordinator", tools_condition)
  builder.add_edge("tools", "coordinator")
  return builder

coordinator_builder = build_coordinator(TOOLS)
whatsapp_coordinator_builder = build_coordinator(WHATSAPP_TOOLS)
