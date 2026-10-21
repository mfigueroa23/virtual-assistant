from collections.abc import Sequence
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from src.agents.context import AgentContext
from src.agents.nodes import TOOLS, build_assistant
from src.agents.tools.knowledge import KNOWLEDGE_TOOLS

def build_sub_agent(tools: Sequence[BaseTool]) -> CompiledStateGraph[MessagesState, AgentContext]:
  builder = StateGraph(MessagesState, context_schema=AgentContext)
  builder.add_node("assistant", build_assistant(tools))
  builder.add_node("tools", ToolNode(tools))
  builder.add_edge(START, "assistant")
  builder.add_conditional_edges("assistant", tools_condition)
  builder.add_edge("tools", "assistant")
  return builder.compile(checkpointer=False)

sub_agent_graph = build_sub_agent(TOOLS)
# Solo el sub-agente de conocimiento puede proponer cambios: los demás siguen con search_faq (D4 del plan 009).
knowledge_graph = build_sub_agent(KNOWLEDGE_TOOLS)
