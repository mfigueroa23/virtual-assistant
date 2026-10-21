import asyncio
import logging
from types import SimpleNamespace
from typing import Any, cast
import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from src.interfaces.google_chat import AddonEvent, ChatAttachment, chat_reply
from src.services import google_chat_reply
from src.services.google_chat_reply import (
    AVISO, AVISO_DATOS, DEMORA, NO_DISPONIBLE, ChatAnswer, answer, cancel_pending, draft_notice, pending, process,
    read_notice_settings, read_thresholds)
from src.utils.exceptions.agents import AgentNotFoundError
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError

GRAPH = cast(Any, object())
SESSION = cast(AsyncSession, None)


@pytest.fixture(autouse=True)
def no_knowledge(monkeypatch):
    """Por defecto el mensaje no viene del space de aviso y el turno no propone cambios: sin BD real."""
    async def fake_allowed(session, space):
        return False

    async def fake_cards(session, turn_id):
        return []

    monkeypatch.setattr(google_chat_reply, "knowledge_allowed", fake_allowed)
    monkeypatch.setattr(google_chat_reply, "turn_cards", fake_cards)


def space_event() -> AddonEvent:
    return AddonEvent.model_validate({"chat": {"messagePayload": {
        "space": {"name": "spaces/AAA", "spaceType": "SPACE"},
        "message": {"text": "@Asistente ¿cómo pago?", "argumentText": " ¿cómo pago?",
                    "thread": {"name": "spaces/AAA/threads/T1"},
                    "sender": {"name": "users/123", "type": "HUMAN"}}}}})


def dm_event() -> AddonEvent:
    return AddonEvent.model_validate({"chat": {"messagePayload": {
        "space": {"name": "spaces/DM1", "spaceType": "DIRECT_MESSAGE"},
        "message": {"text": "¿cómo pago?", "thread": {"name": "spaces/DM1/threads/X9"},
                    "sender": {"name": "users/123", "type": "HUMAN"}}}}})


@pytest.mark.anyio
async def test_process_devuelve_la_respuesta_con_el_hilo_del_mensaje_original(monkeypatch):
    calls: list[str] = []

    async def fake_ask(graph, session, coordinator, audiencia, thread_id, mensaje, **kwargs):
        calls.append(thread_id)
        return "ok"

    monkeypatch.setattr(google_chat_reply, "ask", fake_ask)

    assert await process(GRAPH, space_event(), 1) == ChatAnswer("ok")
    assert calls == ["spaces/AAA/threads/T1"]


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AgentNotFoundError("Agente Interno"), DatabaseUnavailableError("sin conexión")])
async def test_process_devuelve_no_disponible_si_falla_el_agente_o_la_bd(monkeypatch, error):
    async def fake_ask(*args, **kwargs):
        raise error

    monkeypatch.setattr(google_chat_reply, "ask", fake_ask)

    assert await process(GRAPH, space_event(), 1) == ChatAnswer(NO_DISPONIBLE)


@pytest.mark.anyio
async def test_process_devuelve_demora_y_cancela_ask_al_pasar_el_maximo(monkeypatch):
    cancelled = asyncio.Event()

    async def blocked_ask(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(google_chat_reply, "ask", blocked_ask)

    assert await process(GRAPH, space_event(), 0.01) == ChatAnswer(DEMORA)
    assert cancelled.is_set()


def fake_properties(monkeypatch, values: dict[str, str]) -> None:
    async def fake_get_property(session, key):
        if key not in values:
            raise PropertyNotFoundError(key)
        return values[key]

    monkeypatch.setattr(google_chat_reply, "get_property", fake_get_property)


@pytest.mark.anyio
async def test_read_thresholds_lee_el_aviso_y_el_maximo(monkeypatch):
    fake_properties(monkeypatch, {"google_chat_notice_seconds": "5", "google_timeout": "120"})

    assert await read_thresholds(SESSION) == (5, 120)


@pytest.mark.anyio
async def test_read_thresholds_usa_10_segundos_si_falta_el_aviso(monkeypatch):
    fake_properties(monkeypatch, {"google_timeout": "120"})

    assert await read_thresholds(SESSION) == (10, 120)


@pytest.mark.anyio
async def test_read_thresholds_lanza_error_si_falta_el_maximo(monkeypatch):
    fake_properties(monkeypatch, {"google_chat_notice_seconds": "5"})

    with pytest.raises(PropertyNotFoundError):
        await read_thresholds(SESSION)


@pytest.mark.anyio
async def test_read_notice_settings_lee_las_instrucciones_y_el_limite(monkeypatch):
    fake_properties(monkeypatch, {"google_chat_notice_prompt": "Avisa", "google_chat_notice_timeout_seconds": "3"})

    assert await read_notice_settings(SESSION) == ("Avisa", 3)


@pytest.mark.anyio
async def test_read_notice_settings_usa_5_segundos_y_sin_instrucciones_si_faltan(monkeypatch):
    fake_properties(monkeypatch, {})

    assert await read_notice_settings(SESSION) == (None, 5)


PROMPT = "Redacta un aviso breve"
NOTICE = "Reviso tu consulta sobre pagos y te respondo en un mensaje nuevo"
NOTICE_PROPERTIES = {"google_chat_notice_prompt": PROMPT, "google_chat_notice_timeout_seconds": "1"}


class FakeChatModel:
    """LLM falso del aviso: registra los mensajes y responde `text`, espera `release` o lanza `error`."""

    def __init__(self, text: str = NOTICE, release: asyncio.Event | None = None, error: Exception | None = None):
        self.text = text
        self.release = release
        self.error = error
        self.calls: list[list[BaseMessage]] = []
        self.cancelled = False

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        try:
            if self.release is not None:
                await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return AIMessage(self.text)


class Drafting:
    """Sustituye LLM.create en google_chat_reply: entrega `model` o lanza `error`, y cuenta las creaciones."""

    def __init__(self):
        self.model = FakeChatModel()
        self.error: Exception | None = None
        self.created = 0

    async def create(self, session):
        self.created += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(llm = self.model)


@pytest.fixture
def drafting(monkeypatch) -> Drafting:
    fake = Drafting()
    monkeypatch.setattr(google_chat_reply, "LLM", SimpleNamespace(create = fake.create))
    return fake


def attachment_event() -> AddonEvent:
    event = dm_event()
    assert event.chat and event.chat.message_payload and event.chat.message_payload.message
    event.chat.message_payload.message.attachment = [ChatAttachment.model_validate({
        "contentName": "contrato.pdf", "contentType": "application/pdf", "source": "UPLOADED_CONTENT",
        "attachmentDataRef": {"resourceName": "spaces/DM1/attachments/A1"}})]
    return event


@pytest.mark.anyio
async def test_draft_notice_redacta_el_aviso_con_las_instrucciones_y_el_mensaje(monkeypatch, drafting):
    fake_properties(monkeypatch, NOTICE_PROPERTIES)

    assert await draft_notice(space_event()) == NOTICE
    [[system, human]] = drafting.model.calls
    assert isinstance(system, SystemMessage) and system.text == PROMPT
    assert isinstance(human, HumanMessage) and "¿cómo pago?" in human.text and AVISO_DATOS in human.text


@pytest.mark.anyio
async def test_draft_notice_usa_los_nombres_de_los_adjuntos_sin_leerlos_ni_el_historial(monkeypatch, drafting):
    async def unexpected(*args):
        raise AssertionError("el aviso no debe leer los adjuntos")

    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "read_files", unexpected)
    monkeypatch.setattr(google_chat_reply, "download", unexpected)

    assert await draft_notice(attachment_event()) == NOTICE
    [messages] = drafting.model.calls
    assert len(messages) == 2
    assert "contrato.pdf" in messages[1].text


@pytest.mark.anyio
async def test_draft_notice_lee_las_instrucciones_en_cada_aviso(monkeypatch, drafting):
    values = dict(NOTICE_PROPERTIES)
    fake_properties(monkeypatch, values)

    await draft_notice(space_event())
    values["google_chat_notice_prompt"] = "Otras instrucciones"
    await draft_notice(space_event())

    assert [messages[0].text for messages in drafting.model.calls] == [PROMPT, "Otras instrucciones"]


@pytest.mark.anyio
async def test_draft_notice_usa_el_aviso_fijo_sin_llamar_al_llm_si_faltan_las_instrucciones(monkeypatch, drafting):
    fake_properties(monkeypatch, {})

    assert await draft_notice(space_event()) == AVISO
    assert drafting.created == 0


@pytest.mark.anyio
async def test_draft_notice_usa_el_aviso_fijo_si_el_llm_supera_el_limite(monkeypatch, drafting, caplog):
    fake_properties(monkeypatch, {**NOTICE_PROPERTIES, "google_chat_notice_timeout_seconds": "0.01"})
    drafting.model = FakeChatModel(release = asyncio.Event())

    assert await draft_notice(space_event()) == AVISO
    assert drafting.model.cancelled
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize("properties, model_error, create_error", [
    (NOTICE_PROPERTIES, Exception("detalle interno"), None),
    (NOTICE_PROPERTIES, None, PropertyNotFoundError("google_api_key")),
    ({**NOTICE_PROPERTIES, "google_chat_notice_timeout_seconds": "cinco"}, None, None),
])
async def test_draft_notice_usa_el_aviso_fijo_y_registra_el_error_si_falla(
    monkeypatch, drafting, caplog, properties, model_error, create_error
):
    fake_properties(monkeypatch, properties)
    drafting.model = FakeChatModel(error = model_error)
    drafting.error = create_error

    notice = await draft_notice(space_event())

    assert notice == AVISO
    assert "detalle interno" not in notice
    assert any(record.levelno == logging.ERROR for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["", "   \n"])
async def test_draft_notice_usa_el_aviso_fijo_si_el_llm_responde_vacio(monkeypatch, drafting, text):
    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    drafting.model = FakeChatModel(text)

    assert await draft_notice(space_event()) == AVISO


def asking(release: asyncio.Event | None = None):
    """ask falso: responde "ok" en cuanto se libera el evento (o de inmediato si no hay evento)."""
    async def fake_ask(*args, **kwargs):
        if release is not None:
            await release.wait()
        return "ok"
    return fake_ask


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, str]]:
    """Sustituye la cuenta de servicio y la publicación en Chat; devuelve los mensajes publicados."""
    calls: list[tuple[str, str]] = []

    async def fake_chat_token(session, http):
        return "ya29.token"

    async def fake_create_message(space, text, token, http, **kwargs):
        calls.append((space, text))

    monkeypatch.setattr(google_chat_reply, "chat_token", fake_chat_token)
    monkeypatch.setattr(google_chat_reply, "create_message", fake_create_message)
    return calls


async def delivered() -> None:
    await asyncio.gather(*pending)
    await asyncio.sleep(0)


@pytest.mark.anyio
async def test_answer_responde_en_el_hilo_si_termina_antes_del_aviso(monkeypatch, published):
    monkeypatch.setattr(google_chat_reply, "ask", asking())

    assert await answer(GRAPH, space_event(), 0.5, 1) == chat_reply("ok")
    assert published == []
    assert not pending


@pytest.mark.anyio
@pytest.mark.parametrize("event, expected", [
    (space_event, ("spaces/AAA", "<users/123> ok")),
    (dm_event, ("spaces/DM1", "ok")),
])
async def test_answer_avisa_y_publica_la_respuesta_como_mensaje_nuevo(monkeypatch, published, event, expected):
    release = asyncio.Event()
    fake_properties(monkeypatch, {})
    monkeypatch.setattr(google_chat_reply, "ask", asking(release))

    assert await answer(GRAPH, event(), 0.01, 1) == chat_reply(AVISO)
    release.set()
    await delivered()

    assert published == [expected]
    assert not pending


@pytest.mark.anyio
async def test_answer_registra_error_y_no_reintenta_si_falla_la_publicacion(monkeypatch, caplog):
    release = asyncio.Event()
    calls: list[str] = []

    async def fake_chat_token(session, http):
        return "ya29.token"

    async def failing_create_message(space, text, token, http, **kwargs):
        calls.append(text)
        raise httpx.HTTPError("403")

    monkeypatch.setattr(google_chat_reply, "ask", asking(release))
    monkeypatch.setattr(google_chat_reply, "chat_token", fake_chat_token)
    monkeypatch.setattr(google_chat_reply, "create_message", failing_create_message)
    fake_properties(monkeypatch, {})

    assert await answer(GRAPH, space_event(), 0.01, 1) == chat_reply(AVISO)
    release.set()
    await delivered()

    assert calls == ["<users/123> ok"]
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert not pending


@pytest.mark.anyio
@pytest.mark.parametrize("notice, maximum", [(0.02, 0.02), (0.05, 0.01)])
async def test_answer_nunca_avisa_si_el_aviso_no_es_menor_que_el_maximo(monkeypatch, published, notice, maximum):
    monkeypatch.setattr(google_chat_reply, "ask", asking(asyncio.Event()))

    assert await answer(GRAPH, space_event(), notice, maximum) == chat_reply(DEMORA)
    assert published == []
    assert not pending


@pytest.mark.anyio
async def test_answer_atiende_por_separado_dos_mensajes_de_la_misma_conversacion(monkeypatch, published):
    release = asyncio.Event()
    fake_properties(monkeypatch, {})
    monkeypatch.setattr(google_chat_reply, "ask", asking(release))

    assert await answer(GRAPH, space_event(), 0.01, 1) == chat_reply(AVISO)
    assert await answer(GRAPH, space_event(), 0.01, 1) == chat_reply(AVISO)
    release.set()
    await delivered()

    assert published == [("spaces/AAA", "<users/123> ok"), ("spaces/AAA", "<users/123> ok")]
    assert not pending


@pytest.mark.anyio
async def test_cancel_pending_cancela_las_solicitudes_en_curso(monkeypatch, published):
    fake_properties(monkeypatch, {})
    monkeypatch.setattr(google_chat_reply, "ask", asking(asyncio.Event()))
    await answer(GRAPH, space_event(), 0.01, 1)
    await answer(GRAPH, dm_event(), 0.01, 1)
    tasks = list(pending)

    assert await cancel_pending() == 2
    assert len(tasks) == 2 and all(task.cancelled() for task in tasks)
    assert not pending
    assert published == []


async def until(condition) -> None:
    """Espera, como máximo un segundo, a que se cumpla la condición."""
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("la condición no se cumplió a tiempo")


@pytest.mark.anyio
async def test_answer_no_redacta_el_aviso_si_responde_antes_del_umbral(monkeypatch, published, drafting):
    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "ask", asking())

    assert await answer(GRAPH, space_event(), 0.5, 1) == chat_reply("ok")
    assert drafting.created == 0
    assert published == []


@pytest.mark.anyio
@pytest.mark.parametrize("event, expected", [
    (space_event, ("spaces/AAA", "<users/123> ok")),
    (dm_event, ("spaces/DM1", "ok")),
])
async def test_answer_responde_el_aviso_redactado_y_publica_la_respuesta(
    monkeypatch, published, drafting, event, expected
):
    release = asyncio.Event()
    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "ask", asking(release))

    assert await answer(GRAPH, event(), 0.01, 1) == chat_reply(NOTICE)
    release.set()
    await delivered()

    assert published == [expected]
    assert not pending


@pytest.mark.anyio
async def test_answer_responde_en_el_hilo_si_la_respuesta_llega_antes_que_el_aviso(monkeypatch, published, drafting):
    release = asyncio.Event()
    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "ask", asking(release))
    drafting.model = FakeChatModel(release = asyncio.Event())

    replying = asyncio.create_task(answer(GRAPH, space_event(), 0.01, 1))
    await until(lambda: drafting.model.calls)
    release.set()

    assert await replying == chat_reply("ok")
    await until(lambda: drafting.model.cancelled)
    assert published == []
    assert not pending


@pytest.mark.anyio
async def test_answer_no_reinicia_el_maximo_mientras_redacta_el_aviso(monkeypatch, published, drafting):
    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "ask", asking(asyncio.Event()))
    drafting.model = FakeChatModel(release = asyncio.Event())
    start = asyncio.get_running_loop().time()

    assert await answer(GRAPH, space_event(), 0.01, 0.05) == chat_reply(DEMORA)
    # El límite del aviso es 1 s: responder antes demuestra que el máximo siguió contando desde el inicio.
    assert asyncio.get_running_loop().time() - start < 0.5
    assert published == []
    assert not pending


@pytest.mark.anyio
async def test_answer_usa_el_aviso_fijo_si_falla_la_redaccion_y_publica_la_respuesta(
    monkeypatch, published, drafting
):
    release = asyncio.Event()
    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "ask", asking(release))
    drafting.model = FakeChatModel(error = Exception("sin cuota"))

    assert await answer(GRAPH, space_event(), 0.01, 1) == chat_reply(AVISO)
    release.set()
    await delivered()

    assert published == [("spaces/AAA", "<users/123> ok")]
    assert not pending


@pytest.mark.anyio
async def test_answer_no_guarda_el_aviso_en_el_historial(monkeypatch, published, drafting):
    release = asyncio.Event()
    asked: list[tuple[str, str]] = []

    async def fake_ask(graph, session, coordinator, audiencia, thread_id, mensaje, **kwargs):
        asked.append((thread_id, mensaje))
        await release.wait()
        return "ok"

    fake_properties(monkeypatch, NOTICE_PROPERTIES)
    monkeypatch.setattr(google_chat_reply, "ask", fake_ask)

    assert await answer(GRAPH, space_event(), 0.01, 1) == chat_reply(NOTICE)
    release.set()
    await delivered()

    assert asked == [("spaces/AAA/threads/T1", "¿cómo pago?")]
    assert published == [("spaces/AAA", "<users/123> ok")]


# --- spec 009: conocimiento y tarjetas en las respuestas del Agente Interno ---

CARD = {"cardId": "p-1", "card": {"sections": []}}


def knowledge_turn(monkeypatch, allowed: bool, release: asyncio.Event | None = None) -> list[dict[str, Any]]:
    """ask falso que registra turno y exclusión; turn_cards devuelve una tarjeta para ese mismo turno."""
    calls: list[dict[str, Any]] = []

    async def fake_ask(graph, session, coordinator, audiencia, thread_id, mensaje, turn_id = None, exclude = ()):
        calls.append({"turn_id": turn_id, "exclude": tuple(exclude)})
        if release is not None:
            await release.wait()
        return "Te propongo este cambio"

    async def fake_allowed(session, space):
        calls.append({"space": space})
        return allowed

    async def fake_cards(session, turn_id):
        return [CARD] if turn_id == calls[-1]["turn_id"] else []

    monkeypatch.setattr(google_chat_reply, "ask", fake_ask)
    monkeypatch.setattr(google_chat_reply, "knowledge_allowed", fake_allowed)
    monkeypatch.setattr(google_chat_reply, "turn_cards", fake_cards)
    return calls


@pytest.mark.anyio
@pytest.mark.parametrize(("allowed", "exclude"), [(True, ()), (False, ("Conocimiento",))])
async def test_u_k3_process_excluye_el_conocimiento_fuera_del_space_de_aviso(monkeypatch, allowed, exclude):
    calls = knowledge_turn(monkeypatch, allowed)

    await process(GRAPH, dm_event(), 1)

    assert calls[0] == {"space": "spaces/DM1"}
    assert calls[1]["exclude"] == exclude and calls[1]["turn_id"]


@pytest.mark.anyio
async def test_u_t4_process_devuelve_las_tarjetas_del_turno(monkeypatch):
    knowledge_turn(monkeypatch, True)

    assert await process(GRAPH, dm_event(), 1) == ChatAnswer("Te propongo este cambio", [CARD])


@pytest.mark.anyio
async def test_u_t4_cada_mensaje_tiene_su_propio_turno(monkeypatch):
    calls = knowledge_turn(monkeypatch, True)

    await process(GRAPH, dm_event(), 1)
    await process(GRAPH, dm_event(), 1)

    turnos = [call["turn_id"] for call in calls if "turn_id" in call]
    assert len(set(turnos)) == 2


@pytest.mark.anyio
async def test_u_t4_respuesta_sincrona_con_tarjetas(monkeypatch, published):
    knowledge_turn(monkeypatch, True)

    assert await answer(GRAPH, dm_event(), 0.5, 1) == chat_reply("Te propongo este cambio", [CARD])


@pytest.mark.anyio
async def test_u_t4_respuesta_diferida_con_tarjetas(monkeypatch):
    release = asyncio.Event()
    published: list[dict[str, Any]] = []
    fake_properties(monkeypatch, {})
    knowledge_turn(monkeypatch, True, release)

    async def fake_chat_token(session, http):
        return "ya29.token"

    async def fake_create_message(space, text, token, http, cards = (), thread = None):
        published.append({"space": space, "text": text, "cards": list(cards)})

    monkeypatch.setattr(google_chat_reply, "chat_token", fake_chat_token)
    monkeypatch.setattr(google_chat_reply, "create_message", fake_create_message)

    assert await answer(GRAPH, dm_event(), 0.01, 1) == chat_reply(AVISO)
    release.set()
    await delivered()

    assert published == [{"space": "spaces/DM1", "text": "Te propongo este cambio", "cards": [CARD]}]
