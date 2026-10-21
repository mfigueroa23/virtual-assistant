from dataclasses import dataclass
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from src.services.agents import SubAgentConfig

@dataclass
class AgentContext:
  llm: ChatGoogleGenerativeAI
  system_prompt: str
  embeddings: GoogleGenerativeAIEmbeddings
  area: str
  audiencia: str
  # Turno del coordinador: las propuestas de conocimiento se guardan con él para armar sus tarjetas (D2 del plan 009).
  turn_id: str = ""

@dataclass
class CoordinatorContext:
  llm: ChatGoogleGenerativeAI
  system_prompt: str
  embeddings: GoogleGenerativeAIEmbeddings
  audiencia: str
  sub_agents: dict[str, SubAgentConfig]
  # Instrucciones del turno que se suman al final del prompt del coordinador.
  instructions: str = ""
  turn_id: str = ""
