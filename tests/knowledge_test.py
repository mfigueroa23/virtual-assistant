from types import SimpleNamespace
from typing import Any, cast
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from src.agents import coordinator, graph
from src.agents.context import AgentContext, CoordinatorContext
from src.agents.coordinator import coordinator_builder
from src.agents.tools import delegate
from src.agents.tools import knowledge as knowledge_tools
from src.services import assistant
from src.services.agents import SubAgentConfig
from src.services.knowledge import AreaSummary, CategorySummary, KnowledgeHit, Proposal
from src.utils.exceptions.agents import AgentNotFoundError

CONOCIMIENTO = SubAgentConfig("Agente Conocimiento", "Administra las FAQs", "Conocimiento", "Conocimiento del asistente")
OTRA = SubAgentConfig("Agente Soporte", "Ayuda", "Soporte", "Soporte interno")


def tools_of(compiled) -> list[str]:
    return list(compiled.nodes["tools"].bound.tools_by_name)


def proposal(**changes: Any) -> Proposal:
    values: dict[str, Any] = dict(
        id = "p-1", origin = "interno", action = "create", area = "Experiencia", category = "Stack", faq_id = None,
        question = "¿Trabajaste con Kafka?", answer = "Sí, dos años.", previous_question = None, previous_answer = None,
        status = "pending")
    return Proposal(**(values | changes))


class FakeSessionLocal:
    async def __aenter__(self):
        return cast(Any, None)

    async def __aexit__(self, *args):
        return False


class FakeKnowledge:
    """Reemplaza el servicio de conocimiento en las tools: registra las propuestas sin SQL."""

    def __init__(self):
        self.areas = [AreaSummary("Experiencia", "Perfil profesional", [CategorySummary("Stack", 8), CategorySummary("Formación", 3)]),
                      AreaSummary("Blog", "Artículos", [])]
        self.hits = [KnowledgeHit(12, "Experiencia", "Stack", "¿Usas Kafka?", "Sí, dos años.", 0.1)]
        self.proposals: list[dict[str, Any]] = []
        self.result: Proposal | str | None = None
        self.queries: list[list[float]] = []

    async def list_knowledge(self, session):
        return self.areas

    async def search_knowledge(self, session, vector):
        self.queries.append(vector)
        return self.hits

    async def create_proposal(self, session, origin, action, area = "", category = "", question = "", answer = "",
                              faq_id = None, turn_id = None):
        self.proposals.append(dict(origin = origin, action = action, area = area, category = category, question = question,
                                   answer = answer, faq_id = faq_id, turn_id = turn_id))
        return self.result if self.result is not None else proposal(action = action)


class FakeEmbeddings:
    async def aembed_query(self, text):
        return [0.5, 0.5]


@pytest.fixture
def knowledge(monkeypatch) -> FakeKnowledge:
    fake = FakeKnowledge()
    monkeypatch.setattr(knowledge_tools, "SessionLocal", FakeSessionLocal)
    for name in ("list_knowledge", "search_knowledge", "create_proposal"):
        monkeypatch.setattr(knowledge_tools, name, getattr(fake, name))
    return fake


def runtime(turn_id: str = "turno-1") -> Any:
    return SimpleNamespace(context = AgentContext(
        llm = cast(Any, None), system_prompt = "", embeddings = cast(Any, FakeEmbeddings()), area = "Conocimiento",
        audiencia = "interno", turn_id = turn_id))


async def call(tool, **args: Any) -> str:
    return await tool.coroutine(runtime = args.pop("runtime", runtime()), **args)

# --- U-K1, U-K2: grafos y delegación ---

def test_u_k1_solo_el_sub_agente_de_conocimiento_tiene_las_tools_de_escritura():
    assert tools_of(graph.sub_agent_graph) == ["search_faq"]
    assert tools_of(graph.knowledge_graph) == ["ver_conocimiento", "buscar_conocimiento", "proponer_cambio"]
    nombres = [tool.name for tool in coordinator.TOOLS + coordinator.WHATSAPP_TOOLS]
    assert not set(nombres) & {"ver_conocimiento", "buscar_conocimiento", "proponer_cambio"}


class FakeSubAgentGraph:
    def __init__(self):
        self.contexts: list[AgentContext] = []

    async def ainvoke(self, state, context):
        self.contexts.append(context)
        return {"messages": [AIMessage("respuesta")]}


@pytest.mark.anyio
@pytest.mark.parametrize(("audiencia", "area", "expected"), [
    ("interno", "Conocimiento", "knowledge"), ("interno", "Soporte", "faq"), ("externo", "Conocimiento", "faq")])
async def test_u_k2_delegar_area_elige_el_grafo_y_pasa_el_turno(monkeypatch, audiencia, area, expected):
    grafos = {"knowledge": FakeSubAgentGraph(), "faq": FakeSubAgentGraph()}
    monkeypatch.setattr(delegate, "knowledge_graph", grafos["knowledge"])
    monkeypatch.setattr(delegate, "sub_agent_graph", grafos["faq"])
    sub_agent = SubAgentConfig("Agente", "prompt", area, "desc")
    context = CoordinatorContext(llm = cast(Any, None), system_prompt = "", embeddings = cast(Any, None), audiencia = audiencia,
                                 sub_agents = {area: sub_agent}, turn_id = "turno-7")

    respuesta = await cast(Any, delegate.delegar_area).coroutine(area = area, consulta = "¿qué hay?", runtime = SimpleNamespace(context = context))

    assert respuesta == "respuesta"
    assert [context.turn_id for context in grafos[expected].contexts] == ["turno-7"]
    assert grafos["faq" if expected == "knowledge" else "knowledge"].contexts == []

# --- U-K3: exclusión del área de conocimiento en run_turn ---

class FakeCoordinatorModel:
    def __init__(self):
        self.prompts: list[str] = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.prompts.append(messages[0].text)
        return AIMessage("Hola Marco")


def interno_graph(monkeypatch, sub_agents: list[SubAgentConfig]) -> tuple[Any, FakeCoordinatorModel]:
    model = FakeCoordinatorModel()

    async def fake_create(session):
        return SimpleNamespace(llm = model, embeddings = None)

    async def fake_prompt(session, name):
        return "Eres el Agente Interno"

    async def fake_sub_agents(session, audiencia):
        return sub_agents

    monkeypatch.setattr(assistant.LLM, "create", fake_create)
    monkeypatch.setattr(assistant, "get_prompt", fake_prompt)
    monkeypatch.setattr(assistant, "list_sub_agents", fake_sub_agents)
    return coordinator_builder.compile(checkpointer = InMemorySaver()), model


@pytest.mark.anyio
async def test_u_k3_run_turn_excluye_las_areas_y_usa_el_turno_dado(monkeypatch):
    compiled, model = interno_graph(monkeypatch, [CONOCIMIENTO, OTRA])

    turn = await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", "interno", "spaces/AAA", "Hola",
                                    turn_id = "turno-1", exclude = {"Conocimiento"})

    assert turn[-1].text == "Hola Marco"
    assert "- Soporte:" in model.prompts[0] and "Conocimiento" not in model.prompts[0]
    state = await compiled.aget_state({"configurable": {"thread_id": "spaces/AAA"}})
    assert state.values["messages"][0].id == "turno-1"


@pytest.mark.anyio
async def test_u_k3_sin_excluir_el_conocimiento_queda_disponible(monkeypatch):
    compiled, model = interno_graph(monkeypatch, [CONOCIMIENTO, OTRA])

    await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", "interno", "spaces/AAA", "Hola")

    assert "- Conocimiento:" in model.prompts[0]


@pytest.mark.anyio
async def test_u_k3_si_el_conocimiento_es_la_unica_area_igual_responde(monkeypatch):
    compiled, model = interno_graph(monkeypatch, [CONOCIMIENTO])

    turn = await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", "interno", "spaces/OTRO", "Hola",
                                    exclude = {"Conocimiento"})

    assert turn[-1].text == "Hola Marco" and "Conocimiento" not in model.prompts[0]


@pytest.mark.anyio
async def test_u_k3_sin_areas_en_la_audiencia_sigue_sin_agente(monkeypatch):
    compiled, _ = interno_graph(monkeypatch, [])

    with pytest.raises(AgentNotFoundError):
        await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", "interno", "spaces/AAA", "Hola")

# --- U-K4 a U-K8: tools del sub-agente de conocimiento ---

@pytest.mark.anyio
async def test_u_k4_ver_conocimiento_lista_areas_categorias_y_cantidades(knowledge):
    texto = await call(knowledge_tools.ver_conocimiento)

    assert "Experiencia" in texto and "Stack (8)" in texto and "Formación (3)" in texto
    assert "Blog" in texto and "sin categorías" in texto


@pytest.mark.anyio
async def test_u_k5_buscar_conocimiento_devuelve_id_area_categoria_pregunta_y_respuesta(knowledge):
    texto = await call(knowledge_tools.buscar_conocimiento, consulta = "Kafka")

    assert knowledge.queries == [[0.5, 0.5]]
    assert all(dato in texto for dato in ("12", "Experiencia", "Stack", "¿Usas Kafka?", "Sí, dos años."))


@pytest.mark.anyio
async def test_u_k5_buscar_sin_resultados_lo_dice(knowledge):
    knowledge.hits = []

    assert "No se encontraron" in await call(knowledge_tools.buscar_conocimiento, consulta = "Rust")


@pytest.mark.anyio
async def test_u_k6_proponer_crear_guarda_la_propuesta_del_turno_sin_escribir_la_faq(knowledge):
    texto = await call(knowledge_tools.proponer_cambio, accion = "crear", area = "Experiencia", categoria = "Stack",
                       pregunta = "¿Trabajaste con Kafka?", respuesta = "Sí, dos años.")

    assert texto == knowledge_tools.GUIA_PROPUESTA
    assert knowledge.proposals == [dict(origin = "interno", action = "create", area = "Experiencia", category = "Stack",
                                        question = "¿Trabajaste con Kafka?", answer = "Sí, dos años.", faq_id = None,
                                        turn_id = "turno-1")]


def test_u_k6_la_guia_pide_no_dar_el_cambio_por_hecho():
    assert "tarjeta" in knowledge_tools.GUIA_PROPUESTA and "No digas que el cambio está hecho" in knowledge_tools.GUIA_PROPUESTA


@pytest.mark.anyio
@pytest.mark.parametrize(("accion", "action"), [("editar", "update"), ("borrar", "delete")])
async def test_u_k7_editar_y_borrar_proponen_sobre_la_faq_indicada(knowledge, accion, action):
    texto = await call(knowledge_tools.proponer_cambio, accion = accion, faq_id = 12, respuesta = "Sí, tres años.")

    assert texto == knowledge_tools.GUIA_PROPUESTA
    assert knowledge.proposals[0]["action"] == action and knowledge.proposals[0]["faq_id"] == 12


@pytest.mark.anyio
@pytest.mark.parametrize("accion", ["editar", "borrar"])
async def test_u_k7_editar_o_borrar_sin_faq_id_no_propone(knowledge, accion):
    texto = await call(knowledge_tools.proponer_cambio, accion = accion)

    assert "buscar_conocimiento" in texto and knowledge.proposals == []


@pytest.mark.anyio
async def test_u_k7_faq_inexistente_devuelve_el_error_del_servicio(knowledge):
    knowledge.result = "No existe una pregunta frecuente con id 99."

    texto = await call(knowledge_tools.proponer_cambio, accion = "borrar", faq_id = 99)

    assert texto == "No existe una pregunta frecuente con id 99."


@pytest.mark.anyio
async def test_u_k8_area_no_externa_devuelve_las_areas_disponibles(knowledge):
    knowledge.result = "El área «Soporte» no existe. Áreas disponibles: Blog, Experiencia."

    texto = await call(knowledge_tools.proponer_cambio, accion = "crear", area = "Soporte", categoria = "X",
                       pregunta = "¿P?", respuesta = "R")

    assert "Áreas disponibles: Blog, Experiencia" in texto and texto != knowledge_tools.GUIA_PROPUESTA


@pytest.mark.anyio
async def test_proponer_sin_turno_no_guarda_nada(knowledge):
    texto = await call(knowledge_tools.proponer_cambio, accion = "crear_categoria", area = "Blog", categoria = "Nueva",
                       runtime = runtime(turn_id = ""))

    assert knowledge.proposals == [] and texto != knowledge_tools.GUIA_PROPUESTA
