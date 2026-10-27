from types import SimpleNamespace
from typing import Any, cast
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from src.agents import coordinator, graph
from src.agents.context import AgentContext, CoordinatorContext
from src.agents.coordinator import coordinator_builder
from src.agents.tools import delegate
from src.agents.tools import faq as faq_tools
from src.agents.tools import knowledge as knowledge_tools
from src.services import assistant
from src.services.agents import SubAgentConfig
from src.services import knowledge as knowledge_service
from src.services.knowledge import AreaSummary, CategorySummary, KnowledgeHit, Proposal
from src.utils.exceptions.agents import AgentNotFoundError

CONOCIMIENTO = SubAgentConfig("Agente Conocimiento", "Administra las FAQs", "Conocimiento", "Conocimiento del asistente", "interno")
OTRA = SubAgentConfig("Agente Soporte", "Ayuda", "Soporte", "Soporte interno", "interno")


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
        self.areas = [AreaSummary("Experiencia", "Perfil profesional", [CategorySummary("Stack", 8), CategorySummary("Formación", 3)], "externo"),
                      AreaSummary("Blog", "Artículos", [], "externo")]
        self.hits = [KnowledgeHit(12, "Experiencia", "Stack", "¿Usas Kafka?", "Sí, dos años.", 0.1, "externo")]
        self.proposals: list[dict[str, Any]] = []
        self.result: Proposal | str | None = None
        self.queries: list[list[float]] = []
        # Audiencias pedidas en cada lectura o propuesta.
        self.audiencias: list[tuple[str, ...]] = []

    async def list_knowledge(self, session, audiencias = knowledge_service.AUDIENCIAS):
        self.audiencias.append(tuple(audiencias))
        return self.areas

    async def search_knowledge(self, session, vector, audiencias = knowledge_service.AUDIENCIAS):
        self.queries.append(vector)
        self.audiencias.append(tuple(audiencias))
        return self.hits

    async def create_proposal(self, session, origin, action, area = "", category = "", question = "", answer = "",
                              faq_id = None, turn_id = None, audiencias = knowledge_service.AUDIENCIAS):
        self.audiencias.append(tuple(audiencias))
        self.proposals.append(dict(origin = origin, action = action, area = area, category = category, question = question,
                                   answer = answer, faq_id = faq_id, turn_id = turn_id))
        return self.result if self.result is not None else proposal(action = action)

    async def create_area_proposal(self, session, name, audiencia, description, turn_id = None):
        self.proposals.append(dict(action = "area", name = name, audiencia = audiencia, description = description, turn_id = turn_id))
        return self.result if self.result is not None else proposal(action = "area", area = name, category = "", audiencia = audiencia,
                                                                     description = description)


class FakeEmbeddings:
    async def aembed_query(self, text):
        return [0.5, 0.5]


@pytest.fixture
def knowledge(monkeypatch) -> FakeKnowledge:
    fake = FakeKnowledge()
    monkeypatch.setattr(knowledge_tools, "SessionLocal", FakeSessionLocal)
    for name in ("list_knowledge", "search_knowledge", "create_proposal", "create_area_proposal"):
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
    assert tools_of(graph.knowledge_graph) == ["ver_conocimiento", "buscar_conocimiento", "proponer_cambio", "proponer_area"]
    nombres = [tool.name for tool in coordinator.TOOLS + coordinator.WHATSAPP_TOOLS]
    assert not set(nombres) & {"ver_conocimiento", "buscar_conocimiento", "proponer_cambio", "proponer_area"}


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
    sub_agent = SubAgentConfig("Agente", "prompt", area, "desc", audiencia)
    context = CoordinatorContext(llm = cast(Any, None), system_prompt = "", embeddings = cast(Any, None),
                                 sub_agents = {area: sub_agent}, turn_id = "turno-7")

    respuesta = await cast(Any, delegate.delegar_area).coroutine(area = area, consulta = "¿qué hay?", runtime = SimpleNamespace(context = context))

    assert respuesta == "respuesta"
    assert [context.turn_id for context in grafos[expected].contexts] == ["turno-7"]
    assert grafos["faq" if expected == "knowledge" else "knowledge"].contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize("area", ["Amigos", "Experiencia"])
async def test_u_p4_delegar_area_pasa_la_audiencia_del_area_y_no_la_del_coordinador(monkeypatch, area):
    grafo = FakeSubAgentGraph()
    monkeypatch.setattr(delegate, "sub_agent_graph", grafo)
    sub_agents = {"Amigos": SubAgentConfig("Agente Amigos", "prompt", "Amigos", "Amigos", "personal"),
                  "Experiencia": SubAgentConfig("Agente Experiencia", "prompt", "Experiencia", "Perfil", "externo")}
    context = CoordinatorContext(llm = cast(Any, None), system_prompt = "", embeddings = cast(Any, None), sub_agents = sub_agents)

    await cast(Any, delegate.delegar_area).coroutine(area = area, consulta = "¿qué hay?", runtime = SimpleNamespace(context = context))

    assert [(context.area, context.audiencia) for context in grafo.contexts] == [(area, sub_agents[area].audiencia)]


@pytest.mark.anyio
async def test_u_p5_search_faq_busca_en_el_area_y_la_audiencia_del_sub_agente(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_search_faqs(session, vector, area, audiencia):
        calls.append((area, audiencia))
        return []

    monkeypatch.setattr(faq_tools, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(faq_tools, "search_faqs", fake_search_faqs)
    context = AgentContext(llm = cast(Any, None), system_prompt = "", embeddings = cast(Any, FakeEmbeddings()), area = "Amigos",
                           audiencia = "personal")

    await cast(Any, faq_tools.search_faq).coroutine(query = "¿Dónde vives?", runtime = SimpleNamespace(context = context))

    assert calls == [("Amigos", "personal")]

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

    async def fake_sub_agents(session, audiencias):
        return sub_agents

    monkeypatch.setattr(assistant.LLM, "create", fake_create)
    monkeypatch.setattr(assistant, "get_prompt", fake_prompt)
    monkeypatch.setattr(assistant, "list_sub_agents", fake_sub_agents)
    return coordinator_builder.compile(checkpointer = InMemorySaver()), model


@pytest.mark.anyio
async def test_u_k3_run_turn_excluye_las_areas_y_usa_el_turno_dado(monkeypatch):
    compiled, model = interno_graph(monkeypatch, [CONOCIMIENTO, OTRA])

    turn = await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", ("interno",), "spaces/AAA", "Hola",
                                    turn_id = "turno-1", exclude = {"Conocimiento"})

    assert turn[-1].text == "Hola Marco"
    assert "- Soporte:" in model.prompts[0] and "Conocimiento" not in model.prompts[0]
    state = await compiled.aget_state({"configurable": {"thread_id": "spaces/AAA"}})
    assert state.values["messages"][0].id == "turno-1"


@pytest.mark.anyio
async def test_u_k3_sin_excluir_el_conocimiento_queda_disponible(monkeypatch):
    compiled, model = interno_graph(monkeypatch, [CONOCIMIENTO, OTRA])

    await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", ("interno",), "spaces/AAA", "Hola")

    assert "- Conocimiento:" in model.prompts[0]


@pytest.mark.anyio
async def test_u_k3_si_el_conocimiento_es_la_unica_area_igual_responde(monkeypatch):
    compiled, model = interno_graph(monkeypatch, [CONOCIMIENTO])

    turn = await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", ("interno",), "spaces/OTRO", "Hola",
                                    exclude = {"Conocimiento"})

    assert turn[-1].text == "Hola Marco" and "Conocimiento" not in model.prompts[0]


@pytest.mark.anyio
async def test_u_k3_sin_areas_en_la_audiencia_sigue_sin_agente(monkeypatch):
    compiled, _ = interno_graph(monkeypatch, [])

    with pytest.raises(AgentNotFoundError):
        await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", ("interno",), "spaces/AAA", "Hola")

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

# --- spec 011, U-C1 a U-C4: conocimiento en las tres audiencias ---

@pytest.mark.anyio
async def test_u_c1_ver_conocimiento_agrupa_por_audiencia(knowledge):
    knowledge.areas = [
        AreaSummary("Blog", "Artículos", [], "externo"),
        AreaSummary("Conocimiento", "Administra las FAQs", [], "interno"),
        AreaSummary("Soporte", "Soporte interno", [CategorySummary("Accesos", 2)], "interno"),
        AreaSummary("Amigos", "Lo que saben los amigos", [CategorySummary("Planes", 4)], "personal")]

    texto = await call(knowledge_tools.ver_conocimiento)

    assert knowledge.audiencias == [knowledge_service.AUDIENCIAS]
    bloques = {bloque.splitlines()[0]: bloque for bloque in texto.split("\n\n")}
    assert set(bloques) == {"Audiencia externo:", "Audiencia interno:", "Audiencia personal:"}
    assert "- Blog: Artículos" in bloques["Audiencia externo:"]
    assert "- Soporte: Soporte interno" in bloques["Audiencia interno:"] and "Accesos (2)" in bloques["Audiencia interno:"]
    assert "- Amigos: Lo que saben los amigos" in bloques["Audiencia personal:"] and "Planes (4)" in bloques["Audiencia personal:"]


@pytest.mark.anyio
async def test_u_c2_buscar_conocimiento_muestra_la_audiencia_de_cada_faq(knowledge):
    knowledge.hits = [KnowledgeHit(12, "Experiencia", "Stack", "¿Usas Kafka?", "Sí, dos años.", 0.1, "externo"),
                      KnowledgeHit(30, "Amigos", "Planes", "¿Juegas fútbol?", "Los jueves.", 0.2, "personal")]

    texto = await call(knowledge_tools.buscar_conocimiento, consulta = "deportes")

    assert knowledge.audiencias == [knowledge_service.AUDIENCIAS]
    assert "[id 12] externo · Experiencia › Stack" in texto
    assert "[id 30] personal · Amigos › Planes\nPregunta: ¿Juegas fútbol?\nRespuesta: Los jueves." in texto


@pytest.mark.anyio
@pytest.mark.parametrize("area", ["Soporte", "Amigos"])
async def test_u_c3_proponer_cambio_en_un_area_interno_o_personal_no_restringe_la_audiencia(knowledge, area):
    texto = await call(knowledge_tools.proponer_cambio, accion = "crear", area = area, categoria = "General",
                       pregunta = "¿P?", respuesta = "R")

    assert texto == knowledge_tools.GUIA_PROPUESTA
    assert knowledge.audiencias == [knowledge_service.AUDIENCIAS] and knowledge.proposals[0]["area"] == area


def test_u_c4_areas_disponibles_agrupadas_por_audiencia():
    texto = knowledge_service.group_areas([("externo", "Blog"), ("externo", "Experiencia"), ("interno", "Conocimiento"),
                                           ("personal", "Amigos")])

    assert texto == "\n- externo: Blog, Experiencia\n- interno: Conocimiento\n- personal: Amigos"


class AreaLookupSession:
    """Sesión falsa para create_proposal: el área no existe y la lista de áreas sale de `areas`."""

    def __init__(self, areas: list[tuple[str, str]]):
        self.areas = areas
        self.added: list[Any] = []

    async def execute(self, statement):
        rows = SimpleNamespace(all = lambda: self.areas, one_or_none = lambda: None)
        return SimpleNamespace(tuples = lambda: rows)

    def add(self, row):
        self.added.append(row)


@pytest.mark.anyio
async def test_u_c4_area_inexistente_responde_las_areas_por_audiencia_sin_guardar():
    session = AreaLookupSession([("externo", "Blog"), ("personal", "Amigos")])

    texto = await knowledge_service.create_proposal(cast(Any, session), "interno", "create", "Familia", "General", "¿P?", "R")

    assert texto == "El área «Familia» no existe. Áreas disponibles por audiencia:\n- externo: Blog\n- personal: Amigos"
    assert session.added == []

# --- spec 011, U-N1 a U-N7: crear áreas con su sub-agente ---

@pytest.mark.anyio
async def test_u_n1_proponer_area_guarda_la_propuesta_del_turno_sin_crear_nada(knowledge):
    texto = await call(knowledge_tools.proponer_area, nombre = "Amigos", audiencia = "personal", descripcion = "Lo que saben los amigos")

    assert texto == knowledge_tools.GUIA_PROPUESTA
    assert knowledge.proposals == [dict(action = "area", name = "Amigos", audiencia = "personal",
                                        description = "Lo que saben los amigos", turn_id = "turno-1")]


@pytest.mark.anyio
async def test_u_n1_proponer_area_sin_turno_no_guarda_nada(knowledge):
    texto = await call(knowledge_tools.proponer_area, nombre = "Amigos", audiencia = "personal", descripcion = "d",
                       runtime = runtime(turn_id = ""))

    assert texto == knowledge_tools.SIN_TURNO and knowledge.proposals == []


def test_u_n1_proponer_area_limita_la_audiencia_en_el_esquema_de_la_tool():
    assert knowledge_tools.proponer_area.args["audiencia"]["enum"] == ["externo", "interno", "personal"]


@pytest.mark.parametrize("audiencia", ["externo", "interno", "personal"])
def test_u_n2_base_prompt_de_cada_audiencia(audiencia):
    prompt = knowledge_service.base_prompt(audiencia, "Amigos", "Lo que saben los amigos de {Marco}")

    assert "«Amigos»" in prompt and "Lo que saben los amigos de {Marco}" in prompt
    assert "search_faq" in prompt and "no inventes" in prompt
    assert prompt.endswith(knowledge_service.REGLAS[audiencia])


class ApplySession:
    """Sesión falsa para apply: `existing` dice qué consultas de existencia encuentran algo; registra los add."""

    def __init__(self, existing: list[int | None] | None = None):
        self.existing = list(existing or [])
        self.added: list[Any] = []
        self.statements: list[str] = []

    async def scalar(self, statement):
        self.statements.append(str(statement))
        return self.existing.pop(0) if self.existing else None

    async def execute(self, statement):
        self.statements.append(str(statement))
        return SimpleNamespace(tuples = lambda: SimpleNamespace(one_or_none = lambda: None))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = 40 + self.added.index(row)

    async def commit(self):
        pass


def area_row(**changes: Any) -> Any:
    from src.models.knowledge import KnowledgeProposal
    values: dict[str, Any] = dict(id = "p-a", turn_id = "turno-1", origin = "interno", action = "area", area_id = None,
                                  category = None, area_name = "Amigos", area_description = "Lo que saben los amigos",
                                  area_audiencia = "personal", status = "pending")
    return KnowledgeProposal(**(values | changes))


@pytest.mark.anyio
async def test_u_n3_aplicar_un_area_crea_el_area_y_su_agente_y_guarda_el_area_id():
    from src.models.agents import Agents
    from src.models.areas import Area
    session = ApplySession()
    row = area_row()

    outcome = await knowledge_service.apply(cast(Any, session), row, None)

    assert outcome == "applied"
    area, agent = session.added
    assert isinstance(area, Area) and (area.name, area.description, area.audiencia) == ("Amigos", "Lo que saben los amigos", "personal")
    assert isinstance(agent, Agents) and agent.name == "Agente Amigos" and agent.area_id == area.id
    assert agent.prompt == knowledge_service.base_prompt("personal", "Amigos", "Lo que saben los amigos")
    assert row.area_id == area.id


@pytest.mark.anyio
async def test_u_n4_crear_un_area_con_un_nombre_existente_dice_su_audiencia_sin_guardar():
    session = ApplySession(existing = cast(Any, ["externo"]))

    texto = await knowledge_service.create_area_proposal(cast(Any, session), "Experiencia", "personal", "d", "turno-1")

    assert texto == "El área «Experiencia» ya existe en la audiencia externo."
    assert session.added == []


@pytest.mark.anyio
async def test_u_n4_una_propuesta_valida_se_guarda_pendiente_con_el_turno():
    session = ApplySession()

    result = await knowledge_service.create_area_proposal(cast(Any, session), " Amigos ", "personal", " Lo que saben ", "turno-1")

    assert isinstance(result, Proposal)
    (row,) = session.added
    assert (row.action, row.area_name, row.area_description, row.area_audiencia, row.turn_id, row.status, row.area_id) == (
        "area", "Amigos", "Lo que saben", "personal", "turno-1", "pending", None)
    assert (result.area, result.audiencia, result.description, result.category) == ("Amigos", "personal", "Lo que saben", "")


@pytest.mark.anyio
@pytest.mark.parametrize("existing", [[7], [None, 9]])
async def test_u_n5_si_al_aplicar_ya_existe_el_area_o_el_agente_es_duplicado(existing):
    session = ApplySession(existing = existing)
    row = area_row()

    assert await knowledge_service.apply(cast(Any, session), row, None) == "duplicate"
    assert session.added == [] and row.area_id is None


@pytest.mark.anyio
async def test_u_n6_el_sub_agente_nuevo_se_ofrece_en_el_siguiente_turno_sin_reiniciar(monkeypatch):
    catalogo = [OTRA]
    compiled, model = interno_graph(monkeypatch, catalogo)
    await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", ("interno",), "spaces/AAA", "Hola")

    catalogo.append(SubAgentConfig("Agente Proyectos", "prompt", "Proyectos", "Proyectos internos", "interno"))
    await assistant.run_turn(compiled, cast(Any, None), "Agente Interno", ("interno",), "spaces/AAA", "Hola otra vez")

    assert "- Proyectos:" not in model.prompts[0] and "- Proyectos: Proyectos internos" in model.prompts[1]


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "audiencia", "description", "expected"), [
    ("", "personal", "d", "Falta el nombre del área."),
    ("Amigos", "personal", "  ", "Falta la descripción del área."),
    (" ", "personal", "", "Falta el nombre y la descripción del área."),
    ("Amigos", "familia", "d", "La audiencia «familia» no es válida. Audiencias válidas: externo, interno, personal.")])
async def test_u_n7_area_sin_datos_o_con_audiencia_invalida_no_se_guarda(name, audiencia, description, expected):
    session = ApplySession()

    texto = await knowledge_service.create_area_proposal(cast(Any, session), name, audiencia, description, "turno-1")

    assert texto == expected and session.added == [] and session.statements == []
