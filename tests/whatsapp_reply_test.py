from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from src.agents.coordinator import coordinator_builder
from src.interfaces.whatsapp import WhatsappEvent
from src.services import assistant
from src.services.agents import SubAgentConfig

CONTACTO = "+56911111111"
NEGOCIO = "+56951959027"
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo = UTC)


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
