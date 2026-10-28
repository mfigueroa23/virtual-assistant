import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
import pytest
from src.services import faq_learning
from src.services.faq_learning import FaqDraft, propose_faq
from src.services.knowledge import AreaSummary, CategorySummary, KnowledgeHit, Proposal
from src.services.whatsapp import NoticeTarget

CONTACTO = "+56911111111"
HILO = "spaces/DM1/threads/aviso-1"
URL = "https://chatbot.example/api/v1/google-chat/events"
RESPUESTA = "Sí, trabajé dos años con Kafka en Autofin"
NOTICE = NoticeTarget(CONTACTO, "+56951959027", "Sara Pérez", datetime.now(UTC) - timedelta(hours = 1),
                      ("¿Trabajaste con Kafka?",))


def draft(**changes: Any) -> FaqDraft:
    values: dict[str, Any] = dict(reusable = True, already_answered = False, area = "Personal", category = "Trabajo",
                                  question = "¿Trabajaste con Kafka?", answer = "Sí, dos años en Autofin.")
    return FaqDraft(**(values | changes))


class FakeStructured:
    def __init__(self, owner: "FakeModel"):
        self.owner = owner

    async def ainvoke(self, messages):
        self.owner.calls.append(messages)
        await asyncio.sleep(self.owner.delay)
        if self.owner.error is not None:
            raise self.owner.error
        return self.owner.draft


class FakeModel:
    """LLM falso de salida estructurada: devuelve el borrador configurado y registra lo que recibe."""

    def __init__(self):
        self.draft: FaqDraft | dict[str, Any] = draft()
        self.error: Exception | None = None
        self.delay = 0.0
        self.calls: list[list[Any]] = []
        self.schemas: list[Any] = []

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return FakeStructured(self)


class FakeEmbeddings:
    def __init__(self):
        self.queries: list[str] = []

    async def aembed_query(self, text):
        self.queries.append(text)
        return [0.3]


class FakeSessionLocal:
    async def __aenter__(self):
        return cast(Any, None)

    async def __aexit__(self, *args):
        return False


class Learning:
    """Reemplaza el LLM, el conocimiento, las properties y Google Chat de las propuestas."""

    def __init__(self, monkeypatch):
        self.model = FakeModel()
        self.embeddings = FakeEmbeddings()
        self.areas = [AreaSummary("Personal", "Lo que Marco comparte por WhatsApp", [CategorySummary("Trabajo", 2)], "personal"),
                      AreaSummary("Experiencia", "Perfil profesional", [CategorySummary("Stack", 8)], "externo"),
                      AreaSummary("Blog", "Artículos", [], "externo")]
        self.similar = [KnowledgeHit(7, "Experiencia", "Stack", "¿Usas mensajería?", "Uso RabbitMQ.", 0.2, "externo")]
        # Audiencias pedidas a list_knowledge, search_knowledge y create_proposal.
        self.listed: list[tuple[str, ...]] = []
        self.searched: list[tuple[str, ...]] = []
        self.saved_in: list[tuple[str, ...]] = []
        self.created: list[dict[str, Any]] = []
        self.published: list[dict[str, Any]] = []
        self.publish_error: Exception | None = None
        monkeypatch.setattr(faq_learning, "SessionLocal", FakeSessionLocal)
        for name in ("list_knowledge", "search_knowledge", "create_proposal", "get_property", "chat_token", "create_message"):
            monkeypatch.setattr(faq_learning, name, getattr(self, name))
        monkeypatch.setattr(faq_learning.LLM, "create", self.create)

    async def create(self, session):
        return SimpleNamespace(llm = self.model, embeddings = self.embeddings)

    async def list_knowledge(self, session, audiencias = ("externo", "interno", "personal")):
        self.listed.append(tuple(audiencias))
        return [area for area in self.areas if area.audiencia in audiencias]

    async def search_knowledge(self, session, vector, audiencias = ("externo", "interno", "personal")):
        self.searched.append(tuple(audiencias))
        return [hit for hit in self.similar if hit.audiencia in audiencias]

    async def create_proposal(self, session, origin, action, area = "", category = "", question = "", answer = "",
                              faq_id = None, turn_id = None, audiencias = ("externo", "interno", "personal")):
        self.saved_in.append(tuple(audiencias))
        self.created.append(dict(origin = origin, action = action, area = area, category = category, question = question,
                                 answer = answer))
        return Proposal("p-9", origin, action, area, category, None, question, answer, None, None, "pending", "personal")

    async def get_property(self, session, key):
        return {"google_chat_audience": URL}[key]

    async def chat_token(self, session, http):
        return "ya29.token"

    async def create_message(self, space, text, token, http, cards = (), thread = None):
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append(dict(space = space, text = text, cards = list(cards), thread = thread))


@pytest.fixture
def learning(monkeypatch) -> Learning:
    return Learning(monkeypatch)


def request_of(learning: Learning) -> str:
    system, human = learning.model.calls[0]
    assert system.text == faq_learning.PROPONER
    return human.text

# --- U-L1: la propuesta se guarda y se publica en el hilo ---

@pytest.mark.anyio
async def test_u_l1_el_borrador_se_guarda_y_se_publica_en_el_hilo_del_aviso(learning):
    await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.model.schemas == [FaqDraft]
    assert learning.created == [dict(origin = "consulta", action = "create", area = "Personal", category = "Trabajo",
                                     question = "¿Trabajaste con Kafka?", answer = "Sí, dos años en Autofin.")]
    [message] = learning.published
    assert message["space"] == "spaces/DM1" and message["thread"] == HILO and message["text"]
    buttons = [button["text"] for section in message["cards"][0]["card"]["sections"] for widget in section["widgets"]
               for button in widget.get("buttonList", {}).get("buttons", [])]
    assert buttons == ["Guardar", "Descartar"]


@pytest.mark.anyio
async def test_u_l1_el_pedido_trae_las_preguntas_la_respuesta_y_las_areas(learning):
    await propose_faq(NOTICE, HILO, RESPUESTA)

    texto = request_of(learning)
    assert faq_learning.PROPONER_DATOS in texto
    assert "<preguntas>\n- ¿Trabajaste con Kafka?\n</preguntas>" in texto
    assert f"<respuesta_de_marco>\n{RESPUESTA}\n</respuesta_de_marco>" in texto
    assert "<areas>\n- Personal: Lo que Marco comparte por WhatsApp\n  categorías: Trabajo\n</areas>" in texto


@pytest.mark.anyio
async def test_u_l1_acepta_la_salida_como_diccionario(learning):
    learning.model.draft = draft().model_dump()

    await propose_faq(NOTICE, HILO, RESPUESTA)

    assert len(learning.created) == 1 and len(learning.published) == 1

# --- U-L2: sin datos del contacto ---

@pytest.mark.anyio
async def test_u_l2_el_pedido_no_lleva_el_nombre_ni_el_numero_del_contacto(learning):
    await propose_faq(NOTICE, HILO, RESPUESTA)

    texto = request_of(learning)
    assert "Sara" not in texto and "56911111111" not in texto and "11111111" not in texto


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [
    {"answer": "Sí, como le conté a Sara, dos años."},
    {"question": "¿Pérez trabajó con Kafka?"},
    {"answer": "Escríbeme al +56 9 1111 1111."},
    {"answer": "Llama al 9-1111-1111."},
])
async def test_u_l2_un_borrador_con_datos_del_contacto_no_se_propone(learning, changes):
    learning.model.draft = draft(**changes)

    await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.created == [] and learning.published == []

# --- U-L3, U-L4, U-L7: borradores que no se proponen ---

@pytest.mark.anyio
async def test_u_l3_una_respuesta_no_reutilizable_no_se_propone(learning):
    learning.model.draft = draft(reusable = False)

    await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.created == [] and learning.published == []


@pytest.mark.anyio
async def test_u_l4_si_ya_hay_una_faq_que_responde_no_se_propone(learning):
    learning.model.draft = draft(already_answered = True)

    await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.embeddings.queries == ["¿Trabajaste con Kafka?"]
    assert "<faqs_parecidas>\n[externo · Experiencia › Stack] ¿Usas mensajería?\nUso RabbitMQ.\n</faqs_parecidas>" in request_of(learning)
    assert learning.created == [] and learning.published == []


@pytest.mark.anyio
async def test_u_l7_un_area_que_no_esta_en_el_conocimiento_no_se_propone(learning):
    learning.model.draft = draft(area = "Soporte")

    await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.created == [] and learning.published == []

# --- U-L6: los fallos solo dejan el tipo en el log ---

@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["llm", "lento", "publicar"])
async def test_u_l6_los_fallos_no_publican_ni_dejan_el_texto_en_el_log(learning, monkeypatch, caplog, fault):
    if fault == "llm":
        learning.model.error = RuntimeError(f"Gemini rechazó: {RESPUESTA}")
    elif fault == "lento":
        monkeypatch.setattr(faq_learning, "PROPUESTA_LIMITE", 0.01)
        learning.model.delay = 1
    else:
        learning.publish_error = RuntimeError(f"Chat rechazó: {RESPUESTA}")

    with caplog.at_level(logging.INFO, logger = "src.services.faq_learning"):
        await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.published == []
    assert caplog.records
    assert all("Kafka" not in record.getMessage() for record in caplog.records)


# --- spec 011, U-F1 a U-F5: propuestas solo en áreas personal ---

@pytest.mark.anyio
async def test_u_f1_el_pedido_lista_solo_las_areas_personal_y_se_guarda_en_personal(learning):
    await propose_faq(NOTICE, HILO, RESPUESTA)

    areas = request_of(learning).split("<areas>\n")[1].split("\n</areas>")[0]
    assert learning.listed == [("personal",)]
    assert "Personal" in areas and "Experiencia" not in areas and "Blog" not in areas
    assert learning.saved_in == [("personal",)] and learning.created[0]["area"] == "Personal"
    assert "personal · Personal › Trabajo" in json.dumps(learning.published[0]["cards"], ensure_ascii = False)


def test_u_f1_proponer_pide_un_area_personal_de_la_lista():
    assert "una de las áreas personal de la lista" in faq_learning.PROPONER


@pytest.mark.anyio
async def test_u_f2_busca_parecidas_en_personal_y_externo_con_su_audiencia(learning):
    learning.similar.append(KnowledgeHit(8, "Soporte", "Accesos", "¿Clave de la VPN?", "Pídela a TI.", 0.3, "interno"))
    learning.model.draft = draft(already_answered = True)

    await propose_faq(NOTICE, HILO, RESPUESTA)

    texto = request_of(learning)
    assert learning.searched == [("personal", "externo")]
    assert "[externo · Experiencia › Stack] ¿Usas mensajería?" in texto and "VPN" not in texto
    assert learning.created == []


@pytest.mark.anyio
async def test_u_f3_sin_areas_personal_no_llama_al_llm_ni_publica(learning, caplog):
    learning.areas = [area for area in learning.areas if area.audiencia != "personal"]

    with caplog.at_level(logging.INFO, logger = "src.services.faq_learning"):
        await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.model.calls == [] and learning.embeddings.queries == []
    assert learning.created == [] and learning.published == []
    assert caplog.records and all("Kafka" not in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize("area", ["Experiencia", "Inventada"])
async def test_u_f4_un_borrador_fuera_de_las_areas_personal_no_se_guarda(learning, caplog, area):
    learning.model.draft = draft(area = area)

    with caplog.at_level(logging.INFO, logger = "src.services.faq_learning"):
        await propose_faq(NOTICE, HILO, RESPUESTA)

    assert learning.created == [] and learning.published == []
    # U-F5: el log no trae la pregunta ni la respuesta.
    assert caplog.records
    assert all("Kafka" not in record.getMessage() and "dos años" not in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_schedule_proposal_corre_aparte_y_se_cancela_al_apagar(monkeypatch):
    started = asyncio.Event()

    async def blocked(notice, thread, reply):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(faq_learning, "propose_faq", blocked)

    faq_learning.schedule_proposal(NOTICE, HILO, RESPUESTA)
    await asyncio.wait_for(started.wait(), 1)

    assert len(faq_learning.pending) == 1
    assert await faq_learning.cancel_pending() == 1
    assert not faq_learning.pending


# --- spec 013, U-FL-1: hilo con un space inválido ---

@pytest.mark.anyio
async def test_u_fl1_hilo_con_space_invalido_no_publica_la_propuesta(learning, caplog):
    proposal = Proposal("p-9", "consulta", "create", "Personal", "Trabajo", None, "¿Kafka?", "Sí.", None, None, "pending",
                        "personal")

    with caplog.at_level(logging.ERROR, logger = "src.services.faq_learning"):
        await faq_learning.publish_proposal(cast(Any, None), "spaces/a?b/threads/OTRO", proposal)

    assert learning.published == []
    assert [record.levelno for record in caplog.records] == [logging.ERROR]
    assert "OTRO" not in caplog.text and "a?b" not in caplog.text
