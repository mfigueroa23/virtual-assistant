import asyncio
import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from src.interfaces.google_chat import AddonEvent
from src.services import whatsapp_relay
from src.services.whatsapp import NoticeTarget
from src.utils.exceptions.property import PropertyNotFoundError

CONTACTO = "+56911111111"
NEGOCIO = "+56951959027"
HILO = "spaces/DM1/threads/aviso-1"
SESSION = cast(Any, None)
GRAPH = cast(Any, None)


def thread_event(text: str | None = "Sí, trabajé dos años con Kafka", thread: str | None = HILO, **message: Any) -> AddonEvent:
    body: dict[str, Any] = {**message}
    if text is not None:
        body["text"] = text
    if thread is not None:
        body["thread"] = {"name": thread}
    return AddonEvent.model_validate({"chat": {"messagePayload": {
        "space": {"name": "spaces/DM1", "spaceType": "DIRECT_MESSAGE"}, "message": body}}})


class FakeWriter:
    """LLM falso que redacta el mensaje para el contacto; registra lo que recibe."""

    def __init__(self, reply: str = "¡Sí! Trabajé dos años con Kafka 🙂", error: Exception | None = None, delay: float = 0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return AIMessage(self.reply)


class Relay:
    """Reemplaza el acceso a datos, las properties, el LLM y YCloud del relay."""

    def __init__(self, monkeypatch):
        self.notices: dict[str, NoticeTarget] = {
            HILO: NoticeTarget(CONTACTO, NEGOCIO, "Sara", datetime.now(UTC) - timedelta(hours = 1))}
        self.enabled = True
        self.properties = {"ycloud_api_key": "key"}
        self.tone: str | None = None
        self.writer = FakeWriter()
        self.sent: list[tuple[str, str, str]] = []
        self.send_error: Exception | None = None
        self.replies: list[tuple[str, str]] = []
        self.lookups: list[str] = []
        self.scheduled: list[tuple[NoticeTarget, str, str]] = []
        for name in ("find_notice", "channel_enabled", "get_property", "family_tone", "send_text", "add_marco_reply",
                     "conversation_of", "schedule_proposal"):
            monkeypatch.setattr(whatsapp_relay, name, getattr(self, name))
        monkeypatch.setattr(whatsapp_relay.LLM, "create", self.create)

    async def find_notice(self, session, thread):
        self.lookups.append(thread)
        return self.notices.get(thread)

    async def channel_enabled(self, session):
        return self.enabled

    async def get_property(self, session, key):
        if key not in self.properties:
            raise PropertyNotFoundError(key)
        return self.properties[key]

    async def family_tone(self, session, phone):
        return self.tone

    async def create(self, session):
        return SimpleNamespace(llm = self.writer, embeddings = None)

    async def conversation_of(self, graph, phone):
        return [HumanMessage("¿Trabajaste con Kafka?"), AIMessage("Lo confirmo y te cuento")]

    async def send_text(self, api_key, from_, to, body, http):
        self.sent.append((from_, to, body))
        if self.send_error is not None:
            raise self.send_error

    async def add_marco_reply(self, session, phone, message):
        self.replies.append((phone, message.text))

    def schedule_proposal(self, notice, thread, reply):
        self.scheduled.append((notice, thread, reply))


@pytest.fixture
def relay(monkeypatch) -> Relay:
    return Relay(monkeypatch)


async def reply(event: AddonEvent | None = None) -> str | None:
    return await whatsapp_relay.relay_reply(GRAPH, SESSION, event or thread_event())


@pytest.mark.anyio
async def test_u_c1_la_respuesta_se_redacta_y_se_envia_al_contacto(relay):
    result = await reply()

    assert relay.sent == [(NEGOCIO, CONTACTO, "¡Sí! Trabajé dos años con Kafka 🙂")]
    assert result == "Enviado a Sara:\n¡Sí! Trabajé dos años con Kafka 🙂"


@pytest.mark.anyio
@pytest.mark.parametrize(("tone", "expected"), [("Este contacto es de la familia de Marco.\nCon cariño.", "Con cariño."),
                                                (None, whatsapp_relay.PROFESIONAL)])
async def test_u_c2_compose_reply_usa_la_instruccion_el_tono_y_la_respuesta_como_datos(relay, tone, expected):
    relay.tone = tone
    await reply()

    system, human = relay.writer.calls[0]
    assert isinstance(system, SystemMessage) and system.text.startswith(whatsapp_relay.REDACTAR) and expected in system.text
    assert isinstance(human, HumanMessage) and human.text.startswith(whatsapp_relay.REDACTAR_DATOS)
    assert "<conversacion>\nContacto: ¿Trabajaste con Kafka?\nMarco: Lo confirmo y te cuento\n</conversacion>" in human.text
    assert "<respuesta_de_marco>\nSí, trabajé dos años con Kafka\n</respuesta_de_marco>" in human.text


@pytest.mark.anyio
@pytest.mark.parametrize("event", [thread_event(thread = None), thread_event(thread = "spaces/DM1/threads/otro")])
async def test_u_c3_fuera_del_hilo_de_un_aviso_no_es_del_relay(relay, event):
    relay.enabled = False

    assert await reply(event) is None
    assert relay.writer.calls == [] and relay.sent == []


@pytest.mark.anyio
async def test_u_c4_el_historial_guarda_el_texto_enviado_y_no_el_de_marco(relay):
    await reply()

    assert relay.replies == [(CONTACTO, "¡Sí! Trabajé dos años con Kafka 🙂")]


@pytest.mark.anyio
async def test_u_c5_cada_mensaje_del_hilo_se_envia_por_separado(relay):
    await reply(thread_event("Sí, con Kafka"))
    relay.writer.reply = "Y también con RabbitMQ"
    await reply(thread_event("También RabbitMQ"))

    assert [body for _, _, body in relay.sent] == ["¡Sí! Trabajé dos años con Kafka 🙂", "Y también con RabbitMQ"]
    assert len(relay.writer.calls) == 2 and len(relay.replies) == 2


@pytest.mark.anyio
async def test_u_c6_el_aviso_se_busca_en_cada_evento_sin_cache(relay, monkeypatch):
    await reply()
    # Un reinicio deja vacía la memoria del proceso; el aviso sigue en la BD.
    restarted = Relay(monkeypatch)
    await reply()

    assert relay.lookups == [HILO] and restarted.lookups == [HILO]
    assert len(restarted.sent) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("last", [timedelta(hours = 25), None])
async def test_u_c7_con_la_ventana_cerrada_no_se_envia(relay, last):
    relay.notices[HILO] = NoticeTarget(CONTACTO, NEGOCIO, "Sara", None if last is None else datetime.now(UTC) - last)

    result = await reply()

    assert relay.sent == [] and relay.writer.calls == [] and relay.replies == []
    assert result is not None and "24 h" in result and "https://wa.me/56911111111" in result


@pytest.mark.anyio
@pytest.mark.parametrize("writer", [FakeWriter(error = RuntimeError("caído")), FakeWriter(delay = 1), FakeWriter(reply = "  ")])
async def test_u_c8_si_falla_la_redaccion_no_se_envia_nada(relay, monkeypatch, caplog, writer):
    monkeypatch.setattr(whatsapp_relay, "REDACCION_LIMITE", 0.05)
    relay.writer = writer
    with caplog.at_level(logging.WARNING, logger = "src.services.whatsapp_relay"):
        result = await reply()

    assert result == whatsapp_relay.NO_SE_PUDO
    assert relay.sent == [] and relay.replies == []
    assert caplog.records and all("Kafka" not in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    httpx.HTTPStatusError("400", request = httpx.Request("POST", "https://x"), response = httpx.Response(400)),
    httpx.ConnectError("caído"),
])
async def test_u_c9_si_ycloud_falla_se_dice_en_el_hilo(relay, caplog, error):
    relay.send_error = error
    with caplog.at_level(logging.WARNING, logger = "src.services.whatsapp_relay"):
        result = await reply()

    assert result == whatsapp_relay.NO_SE_PUDO
    assert relay.replies == []
    assert caplog.records and all("Kafka" not in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize("text", [None, "   "])
async def test_u_c10_un_mensaje_sin_texto_no_se_envia(relay, text):
    event = thread_event(text, attachment = [{"contentName": "cv.pdf", "contentType": "application/pdf"}])

    assert await reply(event) == whatsapp_relay.SOLO_TEXTO
    assert relay.sent == [] and relay.writer.calls == []


@pytest.mark.anyio
async def test_u_c11_con_el_canal_apagado_no_se_envia(relay):
    relay.enabled = False

    assert await reply() == whatsapp_relay.CANAL_APAGADO
    assert relay.sent == [] and relay.writer.calls == []


# --- spec 009: propuesta de FAQ tras responder una consulta ---

def with_questions(relay: Relay, *questions: str) -> NoticeTarget:
    notice = dataclasses.replace(relay.notices[HILO], questions = questions)
    relay.notices[HILO] = notice
    return notice


@pytest.mark.anyio
async def test_u_l1_responder_una_consulta_programa_la_propuesta(relay):
    notice = with_questions(relay, "¿Trabajaste con Kafka?")

    result = await reply()

    assert result == "Enviado a Sara:\n¡Sí! Trabajé dos años con Kafka 🙂"
    # Con la respuesta original de Marco, no con el mensaje redactado para el contacto.
    assert relay.scheduled == [(notice, HILO, "Sí, trabajé dos años con Kafka")]


@pytest.mark.anyio
async def test_u_l5_un_aviso_sin_consultas_no_programa_propuesta(relay):
    await reply()

    assert relay.sent and relay.scheduled == []


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["send", "window"])
async def test_u_l6_sin_envio_no_hay_propuesta(relay, fault):
    with_questions(relay, "¿Trabajaste con Kafka?")
    if fault == "send":
        relay.send_error = httpx.ConnectError("sin red")
    else:
        relay.notices[HILO] = dataclasses.replace(relay.notices[HILO], last_inbound_at = None)

    await reply()

    assert relay.scheduled == []


@pytest.mark.anyio
async def test_u_l6_si_la_propuesta_falla_el_envio_y_la_confirmacion_no_cambian(relay, monkeypatch):
    from src.services import faq_learning
    with_questions(relay, "¿Trabajaste con Kafka?")
    monkeypatch.setattr(whatsapp_relay, "schedule_proposal", faq_learning.schedule_proposal)

    async def failing(notice, thread, text):
        raise RuntimeError("falló la propuesta")

    monkeypatch.setattr(faq_learning, "propose_faq", failing)

    result = await reply()
    await asyncio.gather(*faq_learning.pending, return_exceptions = True)

    assert result == "Enviado a Sara:\n¡Sí! Trabajé dos años con Kafka 🙂"
    assert relay.sent == [(NEGOCIO, CONTACTO, "¡Sí! Trabajé dos años con Kafka 🙂")]
