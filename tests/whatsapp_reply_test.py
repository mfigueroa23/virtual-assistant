import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from src.agents import coordinator, nodes
from src.agents.coordinator import coordinator_builder
from src.interfaces.whatsapp import WhatsappEvent
from src.services import assistant
from src.services import whatsapp, whatsapp_reply
from src.services.agents import SubAgentConfig
from src.services.whatsapp import Batch, QueuedMessage, Wait, WhatsappSettings
from src.utils.exceptions.agents import AgentNotFoundError
from src.utils.exceptions.attachments import AttachmentTooLargeError
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError

CONTACTO = "+56911111111"
NEGOCIO = "+56951959027"
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo = UTC)
GRAPH = cast(Any, object())


def inbound(message_id: str = "in-1", type_: str = "text", **content: Any) -> dict[str, Any]:
    if type_ == "text" and not content:
        content = {"text": {"body": "Hola"}}
    return {"id": f"evt-{message_id}", "type": "whatsapp.inbound_message.received", "apiVersion": "v2",
            "createTime": "2026-10-20T12:00:00.000Z",
            "whatsappInboundMessage": {"id": message_id, "wamid": f"wamid.{message_id}", "wabaId": "WABA",
                                       "from": CONTACTO, "to": NEGOCIO, "customerProfile": {"name": "Sara"},
                                       "sendTime": "2026-10-20T12:00:00.000Z", "type": type_, **content}}


def echo(message_id: str = "echo-1", send_time: str = "2026-10-20T12:00:00.000Z", body: str = "Hola, soy yo") -> dict[str, Any]:
    return {"id": f"evt-{message_id}", "type": "whatsapp.smb.message.echoes", "apiVersion": "v2",
            "createTime": send_time,
            "whatsappMessage": {"id": message_id, "wamid": f"wamid.{message_id}", "wabaId": "WABA",
                                "from": NEGOCIO, "to": CONTACTO, "type": "text", "text": {"body": body},
                                "status": "sent", "createTime": send_time, "sendTime": send_time}}


def status_update(status: str = "failed") -> dict[str, Any]:
    return {"id": "evt-status", "type": "whatsapp.message.updated", "apiVersion": "v2",
            "createTime": "2026-10-20T12:00:00.000Z",
            "whatsappMessage": {"id": "out-1", "wabaId": "WABA", "from": NEGOCIO, "to": CONTACTO, "type": "text",
                                "status": status, "errorCode": "131047",
                                "errorMessage": "Re-engagement message"}}


MEDIA = {"id": "m1", "link": "https://api.ycloud.com/v2/whatsapp/media/download/m1?sig=x", "mime_type": "image/jpeg",
         "sha256": "abc"}


@pytest.mark.parametrize("payload", [
    inbound(),
    inbound("in-2", "image", image = {**MEDIA, "caption": "mi CV"}),
    inbound("in-3", "audio", audio = {**MEDIA, "mime_type": "audio/ogg; codecs=opus"}),
    inbound("in-4", "document", document = {**MEDIA, "mime_type": "application/pdf", "filename": "cv.pdf"}),
    inbound("in-5", "sticker", sticker = {**MEDIA, "mime_type": "image/webp"}),
    inbound("in-6", "reaction", reaction = {"message_id": "wamid.x", "emoji": "👍"}),
    inbound("in-7", "location", location = {"latitude": -33.4, "longitude": -70.6, "name": "Casa"}),
    echo(),
    status_update(),
])
def test_los_payloads_de_ycloud_se_parsean(payload):
    event = WhatsappEvent.model_validate(payload)

    assert event.id == payload["id"]
    message = event.whatsapp_inbound_message or event.whatsapp_message
    assert message is not None
    assert message.type == payload.get("whatsappInboundMessage", payload.get("whatsappMessage"))["type"]


def test_el_entrante_expone_remitente_destino_y_medio():
    event = WhatsappEvent.model_validate(inbound("in-2", "image", image = {**MEDIA, "caption": "mi CV"}))

    message = event.whatsapp_inbound_message
    assert message is not None
    assert (message.from_, message.to, message.send_time) == (CONTACTO, NEGOCIO, AHORA)
    assert message.image is not None
    assert (message.image.link, message.image.mime_type, message.image.caption) == (MEDIA["link"], "image/jpeg", "mi CV")


def test_el_estado_fallido_expone_el_error():
    event = WhatsappEvent.model_validate(status_update())

    message = event.whatsapp_message
    assert message is not None
    assert (message.status, message.error_code, message.error_message) == ("failed", "131047", "Re-engagement message")


class FakeCoordinatorModel:
    """Modelo falso del coordinador: responde sin llamar tools, así el grafo termina en un paso."""

    def __init__(self, reply: str):
        self.reply = reply

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(self.reply)


def real_graph(monkeypatch, reply: str = "Claro, te cuento"):
    llm = SimpleNamespace(llm = FakeCoordinatorModel(reply), embeddings = None)

    async def fake_create(session):
        return llm

    async def fake_prompt(session, name):
        return "Responde como Marco"

    async def fake_sub_agents(session, audiencia):
        return [SubAgentConfig("Agente Experiencia", "prompt", "Experiencia", "Perfil profesional")]

    monkeypatch.setattr(assistant.LLM, "create", fake_create)
    monkeypatch.setattr(assistant, "get_prompt", fake_prompt)
    monkeypatch.setattr(assistant, "list_sub_agents", fake_sub_agents)
    return coordinator_builder.compile(checkpointer = InMemorySaver())


@pytest.mark.anyio
async def test_u_h1_ask_antepone_el_historial_previo_al_turno(monkeypatch):
    graph = real_graph(monkeypatch)
    previous = [HumanMessage("¿Estás disponible?"), AIMessage("Sí, escríbeme mañana")]

    respuesta = await assistant.ask(graph, cast(Any, None), "Agente WhatsApp", "externo", "whatsapp:+569", "Hola de nuevo",
                                    previous = previous)

    assert respuesta == "Claro, te cuento"
    state = await graph.aget_state({"configurable": {"thread_id": "whatsapp:+569"}})
    assert [(type(message).__name__, message.text) for message in state.values["messages"]] == [
        ("HumanMessage", "¿Estás disponible?"), ("AIMessage", "Sí, escríbeme mañana"),
        ("HumanMessage", "Hola de nuevo"), ("AIMessage", "Claro, te cuento")]


# --- T06: configuración y reglas del acceso a datos ---

def fake_properties(monkeypatch, values: dict[str, str]) -> None:
    async def fake_get_property(session, key):
        if key not in values:
            raise PropertyNotFoundError(key)
        return values[key]

    monkeypatch.setattr(whatsapp, "get_property", fake_get_property)


@pytest.mark.anyio
async def test_u_w22_read_settings_usa_los_valores_por_defecto(monkeypatch):
    fake_properties(monkeypatch, {})

    assert await whatsapp.read_settings(cast(Any, None)) == WhatsappSettings(
        pause = timedelta(hours = 12), grouping = 8.0, maximum = 120.0)


@pytest.mark.anyio
async def test_u_w23_read_settings_lee_los_valores_de_la_bd(monkeypatch):
    fake_properties(monkeypatch, {"whatsapp_pause_hours": "2", "whatsapp_grouping_seconds": "3.5", "whatsapp_timeout": "60"})

    assert await whatsapp.read_settings(cast(Any, None)) == WhatsappSettings(
        pause = timedelta(hours = 2), grouping = 3.5, maximum = 60.0)


def test_next_pause_cuenta_desde_el_mensaje_de_marco():
    assert whatsapp.next_pause(None, AHORA, timedelta(hours = 12)) == AHORA + timedelta(hours = 12)


def test_next_pause_renueva_pero_no_acorta():
    actual = AHORA + timedelta(hours = 12)

    assert whatsapp.next_pause(actual, AHORA + timedelta(hours = 1), timedelta(hours = 12)) == AHORA + timedelta(hours = 13)
    assert whatsapp.next_pause(actual, AHORA - timedelta(hours = 1), timedelta(hours = 12)) == actual


STALE = timedelta(seconds = 180)


def test_claim_decision_sin_pendientes_no_hace_nada():
    assert whatsapp.claim_decision(None, None, None, False, AHORA, STALE) is None


def test_claim_decision_espera_a_que_venza_la_agrupacion():
    assert whatsapp.claim_decision(None, AHORA + timedelta(seconds = 5), None, True, AHORA, STALE) == Wait(5.0)


def test_claim_decision_espera_si_otro_procesa_el_lote():
    assert whatsapp.claim_decision(AHORA - timedelta(seconds = 10), None, None, True, AHORA, STALE) == Wait(1.0)


def test_claim_decision_toma_el_lote_si_el_proceso_anterior_quedo_viejo():
    assert whatsapp.claim_decision(AHORA - timedelta(seconds = 181), None, None, True, AHORA, STALE) == "claim"


def test_claim_decision_toma_el_lote_vencido():
    assert whatsapp.claim_decision(None, AHORA, None, True, AHORA, STALE) == "claim"


def test_claim_decision_no_toma_lotes_en_pausa():
    assert whatsapp.claim_decision(None, AHORA, AHORA + timedelta(hours = 1), True, AHORA, STALE) == "defer"


# --- T07: recepción y despacho de eventos, con la BD reemplazada por un fake en memoria ---

class FakeSession:
    """Sesión falsa: el FakeStore no usa SQL, solo hace falta poder confirmarla y usarla como contexto."""
    commits = 0

    async def commit(self):
        FakeSession.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeStore:
    """Reemplazo en memoria de src/services/whatsapp.py con las mismas reglas puras (next_pause, claim_decision)."""

    def __init__(self, settings: WhatsappSettings):
        self.settings = settings
        self.events: set[str] = set()
        self.conversations: dict[str, SimpleNamespace] = {}
        self.messages: dict[str, tuple[str, str, QueuedMessage]] = {}

    def conversation(self, phone: str, business_phone: str = NEGOCIO) -> SimpleNamespace:
        return self.conversations.setdefault(phone, SimpleNamespace(
            business_phone = business_phone, paused_until = None, batch_due_at = None, processing_since = None, epoch = 0))

    def with_status(self, phone: str, status: str) -> list[QueuedMessage]:
        found = [message for owner, state, message in self.messages.values() if owner == phone and state == status]
        return sorted(found, key = lambda message: (message.sent_at, message.id))

    def set_status(self, ids, status: str) -> None:
        for message_id in ids:
            owner, _, message = self.messages[message_id]
            self.messages[message_id] = (owner, status, message)

    async def read_settings(self, session):
        return self.settings

    async def register_event(self, session, event_id, now):
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True

    async def add_inbound(self, session, phone, business_phone, message, now, grouping):
        conversation = self.conversation(phone, business_phone)
        if conversation.paused_until is not None and conversation.paused_until > now:
            self.messages.setdefault(message.id, (phone, "history", message))
            return False
        self.messages.setdefault(message.id, (phone, "pending", message))
        conversation.batch_due_at = now + timedelta(seconds = grouping)
        return True

    async def add_echo(self, session, phone, business_phone, message, window):
        conversation = self.conversation(phone, business_phone)
        conversation.paused_until = whatsapp.next_pause(conversation.paused_until, message.sent_at, window)
        conversation.epoch += 1
        conversation.batch_due_at = None
        self.set_status([pending.id for pending in self.with_status(phone, "pending")], "history")
        self.messages.setdefault(message.id, (phone, "history", message))

    async def claim_batch(self, session, phone, now, stale):
        conversation = self.conversations.get(phone)
        if conversation is None:
            return None
        pending = self.with_status(phone, "pending")
        decision = whatsapp.claim_decision(conversation.processing_since, conversation.batch_due_at,
                                           conversation.paused_until, bool(pending), now, stale)
        if decision is None or decision == "defer":
            if decision == "defer":
                self.set_status([message.id for message in pending], "history")
            conversation.batch_due_at = None
            return None
        if isinstance(decision, Wait):
            return decision
        for message in self.with_status(phone, "processing"):
            del self.messages[message.id]
        history = self.with_status(phone, "history")
        self.set_status([message.id for message in pending], "processing")
        conversation.processing_since = now
        conversation.batch_due_at = None
        return Batch(phone, conversation.business_phone, conversation.epoch, pending, history)

    async def current_epoch(self, session, phone):
        return self.conversations[phone].epoch

    async def finish_batch(self, session, phone, ids):
        for message_id in ids:
            self.messages.pop(message_id, None)
        self.conversations[phone].processing_since = None

    async def defer_batch(self, session, phone, ids):
        self.set_status(ids, "history")
        self.conversations[phone].processing_since = None

    async def pending_phones(self, session):
        phones: dict[str, datetime] = {}
        for owner, state, message in self.messages.values():
            if state in ("pending", "processing"):
                phones[owner] = max(phones.get(owner, message.sent_at), message.sent_at)
        return list(phones.items())

    async def defer_conversation(self, session, phone):
        ids = [message.id for message in self.with_status(phone, "pending") + self.with_status(phone, "processing")]
        self.set_status(ids, "history")
        self.conversations[phone].processing_since = None
        self.conversations[phone].batch_due_at = None


FAST = WhatsappSettings(pause = timedelta(hours = 12), grouping = 0.05, maximum = 1.0)
STORE_FUNCTIONS = ["read_settings", "register_event", "add_inbound", "add_echo", "claim_batch", "current_epoch",
                   "finish_batch", "defer_batch", "pending_phones", "defer_conversation"]


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore(FAST)
    for name in STORE_FUNCTIONS:
        monkeypatch.setattr(whatsapp_reply, name, getattr(fake, name))
    monkeypatch.setattr(whatsapp_reply, "SessionLocal", FakeSession)
    return fake


@pytest.fixture
def scheduled(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(whatsapp_reply, "schedule", lambda graph, phone: calls.append(phone))
    return calls


async def receive(payload: dict[str, Any]) -> None:
    await whatsapp_reply.handle_event(GRAPH, cast(Any, FakeSession()), WhatsappEvent.model_validate(payload))


@pytest.mark.anyio
async def test_u_w1_evento_repetido_no_se_procesa_dos_veces(store, scheduled):
    await receive(inbound())
    await receive(inbound())

    assert len(store.messages) == 1
    assert scheduled == [CONTACTO]


@pytest.mark.anyio
async def test_u_w2_tipo_de_evento_desconocido_se_ignora(store, scheduled):
    await receive({"id": "evt-x", "type": "contact.created", "apiVersion": "v2"})

    assert store.messages == {} and store.conversations == {} and scheduled == []


@pytest.mark.anyio
async def test_mensaje_de_grupo_se_ignora(store, scheduled):
    payload = inbound()
    payload["whatsappInboundMessage"]["groupId"] = "grupo-1"

    await receive(payload)

    assert store.messages == {} and scheduled == []


@pytest.mark.anyio
async def test_entrante_se_encola_con_su_texto_y_agenda_el_lote(store, scheduled):
    await receive(inbound(text = {"body": "¿Tienes disponibilidad?"}))

    owner, status, message = store.messages["in-1"]
    assert (owner, status, message.direction, message.text) == (CONTACTO, "pending", "inbound", "¿Tienes disponibilidad?")
    assert store.conversations[CONTACTO].business_phone == NEGOCIO
    assert scheduled == [CONTACTO]


@pytest.mark.anyio
async def test_u_w16_echo_pausa_desde_el_mensaje_de_marco(store, scheduled):
    await receive(echo(send_time = "2026-10-20T12:00:00.000Z"))

    conversation = store.conversations[CONTACTO]
    assert conversation.paused_until == AHORA + timedelta(hours = 12)
    assert conversation.epoch == 1
    owner, status, message = store.messages["echo-1"]
    assert (status, message.direction, message.text) == ("history", "echo", "Hola, soy yo")


@pytest.mark.anyio
async def test_u_w17_otro_echo_renueva_y_uno_atrasado_no_acorta(store, scheduled):
    await receive(echo("echo-1", "2026-10-20T12:00:00.000Z"))
    await receive(echo("echo-2", "2026-10-20T14:00:00.000Z"))
    assert store.conversations[CONTACTO].paused_until == AHORA + timedelta(hours = 14)

    await receive(echo("echo-3", "2026-10-20T10:00:00.000Z"))
    assert store.conversations[CONTACTO].paused_until == AHORA + timedelta(hours = 14)


@pytest.mark.anyio
async def test_u_w18_entrante_en_pausa_queda_para_el_historial(store, scheduled):
    store.conversation(CONTACTO).paused_until = datetime.now(UTC) + timedelta(hours = 1)

    await receive(inbound())

    assert store.messages["in-1"][1] == "history"
    assert scheduled == []


@pytest.mark.anyio
async def test_u_w19_echo_con_un_lote_en_espera_lo_pasa_al_historial(store, scheduled):
    await receive(inbound())
    await receive(echo())

    assert store.messages["in-1"][1] == "history"
    assert store.conversations[CONTACTO].batch_due_at is None


@pytest.mark.anyio
async def test_u_w21_entrante_tras_vencer_la_pausa_se_encola(store, scheduled):
    store.conversation(CONTACTO).paused_until = datetime.now(UTC) - timedelta(minutes = 1)

    await receive(inbound())

    assert store.messages["in-1"][1] == "pending"
    assert scheduled == [CONTACTO]


@pytest.mark.anyio
async def test_u_w27_estado_fallido_queda_en_el_log(store, scheduled, caplog):
    with caplog.at_level(logging.WARNING, logger = "src.services.whatsapp_reply"):
        await receive(status_update("failed"))
        await receive({**status_update("delivered"), "id": "evt-status-2"})

    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1
    assert "131047" in warnings[0] and "Re-engagement message" in warnings[0]


@pytest.mark.parametrize(("payload", "line", "responds"), [
    (inbound(), "Hola", True),
    (inbound("i", "image", image = {**MEDIA, "caption": "mi CV"}), "mi CV", True),
    (inbound("i", "audio", audio = MEDIA), None, True),
    (inbound("i", "document", document = {**MEDIA, "filename": "cv.pdf"}), None, True),
    (inbound("i", "sticker", sticker = MEDIA), "[Sticker]", False),
    (inbound("i", "video", video = {**MEDIA, "caption": "mira"}), "[Video] mira", False),
    (inbound("i", "location", location = {"name": "Casa", "address": "Cerrillos"}), "[Ubicación: Casa, Cerrillos]", False),
    (inbound("i", "contacts", contacts = [{"name": {"formatted_name": "Ana"}}]), "[Tarjeta de contacto: Ana]", False),
    (inbound("i", "reaction", reaction = {"message_id": "w", "emoji": "👍"}), "[Reaccionó con 👍]", False),
    (inbound("i", "interactive"), "[Mensaje de tipo interactive]", False),
])
def test_describe_da_la_linea_y_si_genera_respuesta(payload, line, responds):
    message = WhatsappEvent.model_validate(payload).whatsapp_inbound_message
    assert message is not None

    queued = whatsapp_reply.to_queued(message, "inbound")

    assert (queued.text, whatsapp_reply.responds(queued)) == (line, responds)


# --- T08: procesamiento de lotes ---

class FakeYCloud:
    """Reemplazo de src/services/ycloud.py: registra los envíos, las marcas de leído y las descargas."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.read: list[str] = []
        self.send_error: Exception | None = None
        self.files: dict[str, bytes | Exception] = {}

    async def send_text(self, api_key, from_, to, body, http):
        if self.send_error is not None:
            self.sent.append((from_, to, body))
            raise self.send_error
        self.sent.append((from_, to, body))

    async def mark_as_read(self, api_key, inbound_id, http):
        self.read.append(inbound_id)

    async def download_media(self, api_key, link, http):
        item = self.files[link]
        if isinstance(item, Exception):
            raise item
        return item


class FakeAsk:
    """Reemplazo de ask: registra cada llamada y responde `reply`, espera `release` o lanza `error`."""

    def __init__(self, reply: str = "Hola, sí estoy disponible"):
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.error: Exception | None = None

    async def __call__(self, graph, session, coordinator, audiencia, thread_id, mensaje, previous = ()):
        self.calls.append({"coordinator": coordinator, "audiencia": audiencia, "thread_id": thread_id,
                           "mensaje": mensaje, "previous": list(previous)})
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture
def channel(monkeypatch, store):
    ycloud_fake = FakeYCloud()
    ask = FakeAsk()
    forgotten: list[str] = []

    async def fake_get_property(session, key):
        if key == "ycloud_api_key":
            return "key"
        raise PropertyNotFoundError(key)

    async def fake_forget(graph, phone):
        forgotten.append(phone)

    async def fake_create(session):
        return SimpleNamespace(llm = FakeTranscriber(), embeddings = None)

    for name in ("send_text", "mark_as_read", "download_media"):
        monkeypatch.setattr(whatsapp_reply, name, getattr(ycloud_fake, name))
    monkeypatch.setattr(whatsapp_reply, "ask", ask)
    monkeypatch.setattr(whatsapp_reply, "get_property", fake_get_property)
    monkeypatch.setattr(whatsapp_reply, "forget_answer", fake_forget)
    monkeypatch.setattr(whatsapp_reply.LLM, "create", fake_create)
    return SimpleNamespace(store = store, ycloud = ycloud_fake, ask = ask, forgotten = forgotten)


class FakeTranscriber:
    """LLM falso para leer adjuntos y audios: devuelve un texto fijo según el tipo de bloque."""

    async def ainvoke(self, messages):
        kind = messages[-1].content[1]["type"]
        return AIMessage({"audio": "Transcripción del audio", "image": "Texto de la imagen", "file": "Texto del PDF"}[kind])


async def drain() -> None:
    while whatsapp_reply.pending:
        await asyncio.gather(*list(whatsapp_reply.pending), return_exceptions = True)


def text(message_id: str, body: str) -> dict[str, Any]:
    return inbound(message_id, text = {"body": body})


@pytest.mark.anyio
async def test_u_w3_rafaga_de_tres_mensajes_recibe_una_sola_respuesta(channel):
    for message_id, body in [("in-1", "Hola"), ("in-2", "cómo estás"), ("in-3", "te quería consultar algo")]:
        await receive(text(message_id, body))
    await drain()

    assert [call["mensaje"] for call in channel.ask.calls] == ["Hola\n\ncómo estás\n\nte quería consultar algo"]
    assert len(channel.ycloud.sent) == 1


@pytest.mark.anyio
async def test_u_w4_un_mensaje_durante_la_espera_la_reinicia(channel):
    await receive(text("in-1", "Hola"))
    await asyncio.sleep(0.03)
    await receive(text("in-2", "una consulta"))
    await asyncio.sleep(0.03)

    # Ya pasaron 0,06 s desde el primero, pero la espera se reinició con el segundo.
    assert channel.ask.calls == []
    await drain()
    assert [call["mensaje"] for call in channel.ask.calls] == ["Hola\n\nuna consulta"]


@pytest.mark.anyio
async def test_u_w5_responde_con_el_agente_whatsapp_y_envia_el_texto_tal_cual(channel):
    await receive(text("in-1", "Hola"))
    await drain()

    call = channel.ask.calls[0]
    assert (call["coordinator"], call["audiencia"]) == ("Agente WhatsApp", "externo")
    assert channel.ycloud.sent == [(NEGOCIO, CONTACTO, "Hola, sí estoy disponible")]
    assert channel.store.messages == {}


@pytest.mark.anyio
async def test_u_w6_el_hilo_es_propio_de_whatsapp(channel):
    await receive(text("in-1", "Hola"))
    await drain()

    assert channel.ask.calls[0]["thread_id"] == f"whatsapp:{CONTACTO}"


@pytest.mark.anyio
async def test_u_w7_mensaje_durante_el_procesamiento_va_en_el_lote_siguiente(channel):
    channel.ask.release = asyncio.Event()
    await receive(text("in-1", "Hola"))
    await asyncio.wait_for(channel.ask.started.wait(), 1)
    await receive(text("in-2", "¿sigues ahí?"))
    await asyncio.sleep(0.1)

    assert len(channel.ask.calls) == 1
    channel.ask.release.set()
    await drain()
    assert [call["mensaje"] for call in channel.ask.calls] == ["Hola", "¿sigues ahí?"]
    assert len(channel.ycloud.sent) == 2


@pytest.mark.anyio
async def test_u_w9_marca_como_leido_el_ultimo_entrante_del_lote(channel):
    await receive(text("in-1", "Hola"))
    await receive(text("in-2", "una consulta"))
    await drain()

    assert channel.ycloud.read == ["in-2"]


@pytest.mark.anyio
async def test_respuesta_larga_se_envia_en_varias_partes(channel):
    channel.ask.reply = "\n\n".join(["palabra " * 500] * 3)
    await receive(text("in-1", "Cuéntame todo"))
    await drain()

    assert len(channel.ycloud.sent) > 1
    assert all(len(body) <= 4096 for _, _, body in channel.ycloud.sent)


def test_u_w10_el_coordinador_y_el_subagente_solo_consultan():
    assert [tool.name for tool in coordinator.TOOLS] == ["delegar_area"]
    assert [tool.name for tool in nodes.TOOLS] == ["search_faq"]


@pytest.mark.anyio
async def test_historial_de_la_pausa_va_como_mensajes_previos(channel):
    channel.store.conversation(CONTACTO).paused_until = datetime.now(UTC) + timedelta(hours = 1)
    await receive(text("in-1", "¿Me llamas?"))
    await receive(echo("echo-1", "2026-10-20T12:05:00.000Z", "Te llamo en un rato"))
    channel.store.conversations[CONTACTO].paused_until = datetime.now(UTC) - timedelta(seconds = 1)

    await receive(text("in-2", "Gracias"))
    await drain()

    previous = channel.ask.calls[0]["previous"]
    assert [(type(message).__name__, message.text) for message in previous] == [
        ("HumanMessage", "¿Me llamas?"), ("AIMessage", "Te llamo en un rato")]
    assert channel.ask.calls[0]["mensaje"] == "Gracias"
    assert channel.store.messages == {}


@pytest.mark.anyio
async def test_u_w20_echo_durante_la_respuesta_la_descarta(channel):
    channel.ask.release = asyncio.Event()
    await receive(text("in-1", "Hola"))
    await asyncio.wait_for(channel.ask.started.wait(), 1)
    await receive(echo())
    channel.ask.release.set()
    await drain()

    assert channel.ycloud.sent == []
    assert channel.forgotten == [CONTACTO]


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AgentNotFoundError("Agente WhatsApp"), DatabaseUnavailableError("sin conexión")])
async def test_u_w24_si_falla_el_asistente_no_se_envia_nada(channel, caplog, error):
    channel.ask.error = error
    with caplog.at_level(logging.ERROR, logger = "src.services.whatsapp_reply"):
        await receive(text("in-1", "mi clave es 1234"))
        await drain()

    assert channel.ycloud.sent == []
    assert channel.store.messages == {}
    assert caplog.records and all("1234" not in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_u_w24_sin_api_key_no_se_envia_nada(channel, monkeypatch, caplog):
    async def missing(session, key):
        raise PropertyNotFoundError(key)

    monkeypatch.setattr(whatsapp_reply, "get_property", missing)
    with caplog.at_level(logging.ERROR, logger = "src.services.whatsapp_reply"):
        await receive(text("in-1", "Hola"))
        await drain()

    assert channel.ycloud.sent == [] and channel.ask.calls == []
    assert any("ycloud_api_key" in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_u_w25_si_supera_el_tiempo_maximo_se_descarta(channel, caplog):
    channel.store.settings = WhatsappSettings(pause = timedelta(hours = 12), grouping = 0.01, maximum = 0.05)
    channel.ask.release = asyncio.Event()
    with caplog.at_level(logging.WARNING, logger = "src.services.whatsapp_reply"):
        await receive(text("in-1", "Hola"))
        await drain()

    assert channel.ycloud.sent == []
    assert any("tiempo máximo" in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_u_w26_si_ycloud_rechaza_el_envio_no_se_reintenta(channel, caplog):
    channel.ycloud.send_error = httpx.HTTPStatusError("400", request = httpx.Request("POST", "https://x"),
                                                       response = httpx.Response(400))
    with caplog.at_level(logging.ERROR, logger = "src.services.whatsapp_reply"):
        await receive(text("in-1", "Hola"))
        await drain()

    assert len(channel.ycloud.sent) == 1
    assert channel.forgotten == [CONTACTO]
    assert any("YCloud" in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_u_h2_forget_answer_quita_la_respuesta_del_historial(monkeypatch):
    graph = real_graph(monkeypatch, reply = "Te confirmo el martes")
    await assistant.ask(graph, cast(Any, None), "Agente WhatsApp", "externo", f"whatsapp:{CONTACTO}", "¿Cuándo nos vemos?")

    await whatsapp_reply.forget_answer(graph, CONTACTO)

    state = await graph.aget_state({"configurable": {"thread_id": f"whatsapp:{CONTACTO}"}})
    assert [(type(message).__name__, message.text) for message in state.values["messages"]] == [
        ("HumanMessage", "¿Cuándo nos vemos?")]


# --- T09: medios y mensajes no soportados ---

def media(link: str, mime: str, **extra: Any) -> dict[str, Any]:
    return {"id": link, "link": f"https://api.ycloud.com/v2/whatsapp/media/download/{link}", "mime_type": mime, **extra}


@pytest.mark.anyio
async def test_u_w11_imagen_con_leyenda_se_lee_y_se_suma_al_lote(channel):
    channel.ycloud.files[media("img", "image/jpeg")["link"]] = b"jpg"
    await receive(inbound("in-1", "image", image = media("img", "image/jpeg", caption = "mi CV")))
    await drain()

    assert channel.ask.calls[0]["mensaje"] == "mi CV\n[Archivo adjunto: imagen]\nTexto de la imagen"


@pytest.mark.anyio
async def test_u_w12_nota_de_voz_se_transcribe(channel):
    channel.ycloud.files[media("voz", "audio/ogg; codecs=opus")["link"]] = b"ogg"
    await receive(text("in-1", "Te dejo un audio"))
    await receive(inbound("in-2", "audio", audio = media("voz", "audio/ogg; codecs=opus")))
    await drain()

    assert channel.ask.calls[0]["mensaje"] == "Te dejo un audio\n\n[Nota de voz]\nTranscripción del audio"


@pytest.mark.anyio
@pytest.mark.parametrize(("error", "line"), [
    (httpx.ConnectError("caído"), "[Archivo adjunto: cv.pdf · no se pudo leer]"),
    (AttachmentTooLargeError("cv"), "[Archivo adjunto: cv.pdf · supera los 20 MB, no se leyó]"),
])
async def test_u_w13_documento_que_no_se_puede_leer_deja_su_linea(channel, error, line):
    channel.ycloud.files[media("cv", "application/pdf")["link"]] = error
    await receive(text("in-1", "Te mando mi CV"))
    await receive(inbound("in-2", "document", document = media("cv", "application/pdf", filename = "cv.pdf")))
    await drain()

    assert channel.ask.calls[0]["mensaje"] == f"Te mando mi CV\n\n{line}"
    assert len(channel.ycloud.sent) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(("error", "line"), [
    (httpx.ConnectError("caído"), "[Nota de voz · no se pudo leer]"),
    (AttachmentTooLargeError("voz"), "[Nota de voz · supera los 20 MB, no se leyó]"),
])
async def test_nota_de_voz_que_no_se_puede_leer_deja_su_linea(channel, error, line):
    channel.ycloud.files[media("voz", "audio/ogg")["link"]] = error
    await receive(inbound("in-1", "audio", audio = media("voz", "audio/ogg")))
    await drain()

    assert channel.ask.calls[0]["mensaje"] == line


@pytest.mark.anyio
async def test_u_w14_sticker_con_texto_se_responde_el_texto(channel):
    await receive(text("in-1", "Hola"))
    await receive(inbound("in-2", "sticker", sticker = media("st", "image/webp")))
    await drain()

    assert channel.ask.calls[0]["mensaje"] == "Hola\n\n[Sticker]"
    assert len(channel.ycloud.sent) == 1


@pytest.mark.anyio
async def test_u_w15_lote_de_solo_sticker_y_reaccion_no_se_responde(channel):
    await receive(inbound("in-1", "sticker", sticker = media("st", "image/webp")))
    await receive(inbound("in-2", "reaction", reaction = {"message_id": "w", "emoji": "👍"}))
    await drain()

    assert channel.ask.calls == [] and channel.ycloud.sent == []
    assert {status for _, status, _ in channel.store.messages.values()} == {"history"}


# --- T10: recuperación al arrancar y cancelación al apagar ---

@pytest.mark.anyio
async def test_u_w8_recover_retoma_lotes_recientes_y_archiva_los_viejos(store, scheduled):
    now = datetime.now(UTC)
    reciente = QueuedMessage("in-a", "inbound", now - timedelta(minutes = 5), "text", "Hola")
    viejo = QueuedMessage("in-b", "inbound", now - timedelta(hours = 2), "text", "¿Hola?")
    store.conversation("+56922222222")
    store.conversation("+56933333333")
    store.messages["in-a"] = ("+56922222222", "pending", reciente)
    store.messages["in-b"] = ("+56933333333", "processing", viejo)

    await whatsapp_reply.recover(GRAPH)

    assert scheduled == ["+56922222222"]
    assert store.messages["in-a"][1] == "pending"
    assert store.messages["in-b"][1] == "history"


@pytest.mark.anyio
async def test_cancel_pending_cancela_los_lotes_en_curso(caplog):
    async def blocked() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    whatsapp_reply.pending.add(task)
    task.add_done_callback(whatsapp_reply.pending.discard)

    with caplog.at_level(logging.WARNING, logger = "src.services.whatsapp_reply"):
        assert await whatsapp_reply.cancel_pending() == 1

    assert task.cancelled() and not whatsapp_reply.pending
    assert any("1" in record.getMessage() for record in caplog.records)
