import asyncio
import json
import logging
from types import SimpleNamespace
from typing import cast
import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession
from main import app
from src.routers import google_chat as google_chat_router
from src.interfaces.google_chat import AddonEvent, chat_reply, update_message
from src.services import google_chat, google_chat_reply
from src.services.attachments import Attachment
from src.services.google_chat import (
    CHAT_SCOPE, attachments_of, chat_token, conversation_key, create_message, download, mention_of, message_text, service_account_token,
    valid_attachment_name, valid_space, verify_addon_token)
from src.utils.exceptions.attachments import AttachmentTooLargeError
from src.utils.exceptions.google_chat import InvalidChatNameError, InvalidGoogleTokenError
from src.utils.exceptions.property import PropertyNotFoundError

AUDIENCE = "https://chatbot.example/api/v1/google-chat/events"
ACCOUNT = "service-1@gcp-sa-gsuiteaddons.iam.gserviceaccount.com"
TOKEN_URI = "https://oauth2.googleapis.com/token"
VALID = {"iss": "https://accounts.google.com", "email": ACCOUNT, "email_verified": True}


def space_event() -> dict:
    return {"chat": {"messagePayload": {
        "space": {"name": "spaces/AAA", "spaceType": "SPACE"},
        "message": {"text": "@Asistente ¿cómo pago?", "argumentText": " ¿cómo pago?",
                    "thread": {"name": "spaces/AAA/threads/T1"}}}}}


def dm_event() -> dict:
    return {"chat": {"messagePayload": {
        "space": {"name": "spaces/DM1", "spaceType": "DIRECT_MESSAGE"},
        "message": {"text": "¿cómo pago?", "thread": {"name": "spaces/DM1/threads/X9"}}}}}


@pytest.fixture(autouse=True)
def no_relay(monkeypatch):
    """Por defecto ningún mensaje es respuesta a un aviso de WhatsApp: el endpoint sigue con el Agente Interno."""
    async def fake_relay(graph, session, event):
        return None

    async def fake_allowed(session, space):
        return False

    async def fake_cards(session, turn_id):
        return []

    monkeypatch.setattr(google_chat_router, "relay_reply", fake_relay)
    monkeypatch.setattr(app.state, "whatsapp_graph", object(), raising=False)
    # Sin BD real: el mensaje no es del space de aviso y el turno no propone cambios (spec 009).
    monkeypatch.setattr(google_chat_reply, "knowledge_allowed", fake_allowed)
    monkeypatch.setattr(google_chat_reply, "turn_cards", fake_cards)


@pytest.fixture
def claims(monkeypatch):
    """Simula la decodificación del ID token: los tests no descargan certificados de Google."""
    decoded = dict(VALID)

    async def certs(http):
        return {"kid": "cert"}

    def decode(token, certs, audience):
        if audience != AUDIENCE:
            raise ValueError("audiencia incorrecta")
        return decoded

    monkeypatch.setattr(google_chat, "get_google_certs", certs)
    monkeypatch.setattr(google_chat.jwt, "decode", decode)
    return decoded


async def verify(token: str | None) -> None:
    async with httpx.AsyncClient() as http:
        await verify_addon_token(token, AUDIENCE, ACCOUNT, http)


@pytest.mark.anyio
async def test_token_valido_de_la_cuenta_del_complemento(claims):
    await verify("token")


@pytest.mark.anyio
@pytest.mark.parametrize("token, change", [
    (None, {}),
    ("token", {"iss": "https://evil.example"}),
    ("token", {"email": "otra@gcp-sa-gsuiteaddons.iam.gserviceaccount.com"}),
    ("token", {"email_verified": False}),
])
async def test_token_invalido_lanza_error(claims, token, change):
    claims.update(change)

    with pytest.raises(InvalidGoogleTokenError):
        await verify(token)


def test_key_es_el_hilo_en_un_space_y_el_space_en_un_dm():
    assert conversation_key(AddonEvent.model_validate(space_event())) == "spaces/AAA/threads/T1"
    assert conversation_key(AddonEvent.model_validate(dm_event())) == "spaces/DM1"


def test_text_usa_argument_text_en_un_space_y_el_texto_en_un_dm():
    assert message_text(AddonEvent.model_validate(space_event())) == "¿cómo pago?"
    assert message_text(AddonEvent.model_validate(dm_event())) == "¿cómo pago?"


def test_sender_se_lee_del_evento():
    payload = space_event()
    payload["chat"]["messagePayload"]["message"]["sender"] = {"name": "users/123", "type": "HUMAN"}

    message = AddonEvent.model_validate(payload).chat.message_payload.message  # pyright: ignore[reportOptionalMemberAccess] -- el evento de prueba siempre trae chat y messagePayload

    assert message is not None
    assert message.sender is not None
    assert message.sender.name == "users/123"


def test_sender_es_none_si_el_evento_no_lo_trae():
    message = AddonEvent.model_validate(space_event()).chat.message_payload.message  # pyright: ignore[reportOptionalMemberAccess] -- el evento de prueba siempre trae chat y messagePayload

    assert message is not None
    assert message.sender is None


def with_sender(payload: dict) -> dict:
    payload["chat"]["messagePayload"]["message"]["sender"] = {"name": "users/123", "type": "HUMAN"}
    return payload


def test_mention_of_menciona_al_remitente_en_un_space():
    assert mention_of(AddonEvent.model_validate(with_sender(space_event()))) == "<users/123>"


def test_mention_of_es_vacio_en_un_dm_y_sin_remitente():
    assert mention_of(AddonEvent.model_validate(with_sender(dm_event()))) == ""
    assert mention_of(AddonEvent.model_validate(space_event())) == ""


def test_endpoint_sin_token_responde_401_antes_de_leer_la_configuracion():
    response = TestClient(app).post("/api/v1/google-chat/events", json=space_event())

    assert response.status_code == 401


def test_endpoint_delega_en_answer_y_devuelve_su_respuesta(monkeypatch):
    calls: list[tuple[float, float]] = []

    async def fake_verify_token(session, credentials):
        return None

    async def fake_read_thresholds(session):
        return 10.0, 120.0

    async def fake_answer(graph, event, notice, maximum):
        calls.append((notice, maximum))
        return {"respuesta": "de answer"}

    monkeypatch.setattr(google_chat_router, "verify_token", fake_verify_token)
    monkeypatch.setattr(google_chat_router, "read_thresholds", fake_read_thresholds)
    monkeypatch.setattr(google_chat_router, "answer", fake_answer)
    monkeypatch.setattr(app.state, "graph", object(), raising=False)

    response = TestClient(app).post("/api/v1/google-chat/events", json=space_event())

    assert response.status_code == 200
    assert response.json() == {"respuesta": "de answer"}
    assert calls == [(10.0, 120.0)]


def relay_endpoint(monkeypatch, relayed: str | None) -> dict[str, list]:
    """Endpoint con el token, los umbrales, answer y relay_reply reemplazados."""
    calls: dict[str, list] = {"relay": [], "answer": []}

    async def fake_verify_token(session, credentials):
        return None

    async def fake_read_thresholds(session):
        return 10.0, 120.0

    async def fake_relay(graph, session, event):
        calls["relay"].append(graph)
        return relayed

    async def fake_answer(graph, event, notice, maximum):
        calls["answer"].append(graph)
        return {"respuesta": "del Agente Interno"}

    monkeypatch.setattr(google_chat_router, "verify_token", fake_verify_token)
    monkeypatch.setattr(google_chat_router, "read_thresholds", fake_read_thresholds)
    monkeypatch.setattr(google_chat_router, "relay_reply", fake_relay)
    monkeypatch.setattr(google_chat_router, "answer", fake_answer)
    monkeypatch.setattr(app.state, "graph", object(), raising=False)
    monkeypatch.setattr(app.state, "whatsapp_graph", object(), raising=False)
    return calls


def test_e7_respuesta_en_el_hilo_de_un_aviso_va_al_contacto(monkeypatch):
    calls = relay_endpoint(monkeypatch, "Enviado a Sara:\n¡Sí!")

    response = TestClient(app).post("/api/v1/google-chat/events", json=dm_event())

    assert response.status_code == 200
    assert response.json() == chat_reply("Enviado a Sara:\n¡Sí!")
    assert calls["relay"] == [app.state.whatsapp_graph] and calls["answer"] == []


def test_e8_mensaje_fuera_de_un_aviso_va_al_agente_interno(monkeypatch):
    calls = relay_endpoint(monkeypatch, None)

    response = TestClient(app).post("/api/v1/google-chat/events", json=dm_event())

    assert response.json() == {"respuesta": "del Agente Interno"}
    assert calls["answer"] == [app.state.graph]


def test_endpoint_responde_el_aviso_redactado_por_el_llm(monkeypatch):
    notice = "Reviso tu consulta sobre pagos y te respondo en un mensaje nuevo"
    properties = {"google_chat_notice_prompt": "Redacta un aviso breve", "google_chat_notice_timeout_seconds": "1"}

    async def fake_verify_token(session, credentials):
        return None

    async def fake_read_thresholds(session):
        return 0.01, 1.0

    async def slow_ask(*args, **kwargs):
        await asyncio.sleep(0.5)
        return "ok"

    async def fake_get_property(session, key):
        if key not in properties:
            raise PropertyNotFoundError(key)
        return properties[key]

    class FakeChatModel:
        async def ainvoke(self, messages):
            return AIMessage(notice)

    async def fake_create(session):
        return SimpleNamespace(llm = FakeChatModel())

    async def fake_chat_token(session, http):
        return "ya29.token"

    async def fake_create_message(space, text, token, http, **kwargs):
        return None

    monkeypatch.setattr(google_chat_router, "verify_token", fake_verify_token)
    monkeypatch.setattr(google_chat_router, "read_thresholds", fake_read_thresholds)
    monkeypatch.setattr(google_chat_reply, "ask", slow_ask)
    monkeypatch.setattr(google_chat_reply, "get_property", fake_get_property)
    monkeypatch.setattr(google_chat_reply, "LLM", SimpleNamespace(create = fake_create))
    monkeypatch.setattr(google_chat_reply, "chat_token", fake_chat_token)
    monkeypatch.setattr(google_chat_reply, "create_message", fake_create_message)
    monkeypatch.setattr(app.state, "graph", object(), raising=False)

    response = TestClient(app).post("/api/v1/google-chat/events", json=space_event())

    assert response.status_code == 200
    assert response.json() == chat_reply(notice)


def test_router_no_llama_al_asistente_ni_lee_adjuntos():
    assert not hasattr(google_chat_router, "ask")
    assert not hasattr(google_chat_router, "read_attachments")
    assert not hasattr(google_chat_router, "read_files")


def test_attachments_solo_descarga_los_archivos_subidos_al_chat():
    event = dm_event()
    event["chat"]["messagePayload"]["message"]["attachment"] = [
        {"contentName": "contrato.pdf", "contentType": "application/pdf", "source": "UPLOADED_CONTENT",
         "attachmentDataRef": {"resourceName": "spaces/DM1/attachments/A1"}},
        {"contentName": "informe.docx", "contentType": "application/octet-stream", "source": "DRIVE_FILE",
         "driveDataRef": {"driveFileId": "D1"}}]

    assert attachments_of(AddonEvent.model_validate(event)) == [
        Attachment("contrato.pdf", "application/pdf", "spaces/DM1/attachments/A1"),
        Attachment("informe.docx", "application/octet-stream", None)]
    assert attachments_of(AddonEvent.model_validate(space_event())) == []


def mock_http(content: bytes, seen: list[httpx.Request]) -> httpx.AsyncClient:
    """Cliente con un transporte falso: los tests no llaman a Google."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == TOKEN_URI:
            return httpx.Response(200, json={"access_token": "ya29.token"})
        return httpx.Response(200, content=content)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_token_de_la_cuenta_de_servicio_con_scope_chat_bot(monkeypatch):
    class FakeSigner:
        key_id = None

        def sign(self, message):
            return b"firma"

    monkeypatch.setattr(google_chat.crypt.RSASigner, "from_service_account_info", lambda info: FakeSigner())
    seen: list[httpx.Request] = []
    async with mock_http(b"", seen) as http:
        token = await service_account_token({"client_email": "bot@proyecto.iam.gserviceaccount.com",
                                             "private_key": "clave", "token_uri": TOKEN_URI}, http)

    form = dict(item.split("=", 1) for item in seen[0].content.decode().split("&"))
    claims = google_chat.jwt.decode(form["assertion"], verify=False)
    assert token == "ya29.token" and claims["scope"] == CHAT_SCOPE and claims["aud"] == TOKEN_URI
    assert claims["iss"] == "bot@proyecto.iam.gserviceaccount.com"


@pytest.mark.anyio
async def test_download_con_el_token_y_corta_al_pasar_el_maximo(monkeypatch):
    monkeypatch.setattr(google_chat, "MAX_BYTES", 1000)
    seen: list[httpx.Request] = []
    async with mock_http(b"x" * 1000, seen) as http:
        data = await download("spaces/A/attachments/1", "ya29.token", http)
    async with mock_http(b"x" * 1001, []) as http:
        with pytest.raises(AttachmentTooLargeError):
            await download("spaces/A/attachments/1", "ya29.token", http)

    assert data == b"x" * 1000
    assert seen[0].url.path == "/v1/media/spaces/A/attachments/1" and seen[0].url.params["alt"] == "media"
    assert seen[0].headers["Authorization"] == "Bearer ya29.token"


@pytest.mark.anyio
async def test_create_message_publica_en_el_space_sin_hilo():
    seen: list[httpx.Request] = []
    created = b'{"name": "spaces/AAA/messages/m1", "thread": {"name": "spaces/AAA/threads/t1"}}'
    async with mock_http(created, seen) as http:
        # Devuelve el hilo que abrió el mensaje, para reconocer las respuestas en él (D2 del plan 008).
        assert await create_message("spaces/AAA", "<users/123> ok", "ya29.token", http) == "spaces/AAA/threads/t1"

    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://chat.googleapis.com/v1/spaces/AAA/messages"
    assert seen[0].headers["Authorization"] == "Bearer ya29.token"
    body = json.loads(seen[0].content)
    assert body == {"text": "<users/123> ok"} and "thread" not in body


@pytest.mark.anyio
async def test_create_message_sin_hilo_en_la_respuesta_devuelve_none():
    async with mock_http(b"{}", []) as http:
        assert await create_message("spaces/AAA", "ok", "ya29.token", http) is None


@pytest.mark.anyio
async def test_create_message_lanza_error_si_chat_rechaza_la_publicacion():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await create_message("spaces/AAA", "ok", "ya29.token", http)


async def missing_property(session, key):
    raise PropertyNotFoundError(key)


async def invalid_json(session, key):
    return "no es json"


@pytest.mark.anyio
@pytest.mark.parametrize("fake_property", [missing_property, invalid_json])
async def test_chat_token_es_none_y_registra_error_sin_la_cuenta_de_servicio(monkeypatch, caplog, fake_property):
    monkeypatch.setattr(google_chat, "get_property", fake_property)

    async with mock_http(b"", []) as http:
        token = await chat_token(cast(AsyncSession, None), http)

    assert token is None
    assert any(record.levelno == logging.ERROR for record in caplog.records)

# --- spec 009: tarjetas y clics ---

def click_event(space: str = "spaces/DM1", decision: str = "apply") -> dict:
    return {
        "commonEventObject": {"parameters": {"proposal": "p-1", "decision": decision}},
        "chat": {"buttonClickedPayload": {
            "space": {"name": space, "spaceType": "DIRECT_MESSAGE"},
            "message": {"text": "Te propongo esto", "cardsV2": [{"cardId": "p-1", "card": {"sections": []}}]}}}}


def test_e11_addon_event_lee_el_clic_y_sus_parametros():
    event = AddonEvent.model_validate(click_event())

    assert event.chat is not None and event.chat.button_clicked_payload is not None
    assert event.chat.button_clicked_payload.space is not None
    assert event.chat.button_clicked_payload.space.name == "spaces/DM1"
    message = event.chat.button_clicked_payload.message
    assert message is not None and message.cards_v2[0]["cardId"] == "p-1"
    assert event.common_event_object is not None
    assert event.common_event_object.parameters == {"proposal": "p-1", "decision": "apply"}


def test_e11_chat_reply_y_update_message_con_tarjetas():
    card = {"cardId": "p-1", "card": {"sections": []}}

    assert chat_reply("ok") == {"hostAppDataAction": {"chatDataAction": {"createMessageAction": {"message": {"text": "ok"}}}}}
    assert chat_reply("ok", [card])["hostAppDataAction"]["chatDataAction"]["createMessageAction"]["message"] == {
        "text": "ok", "cardsV2": [card]}
    assert update_message([card], "ok") == {"hostAppDataAction": {"chatDataAction": {"updateMessageAction": {
        "message": {"text": "ok", "cardsV2": [card]}}}}}


@pytest.mark.anyio
async def test_e11_create_message_con_tarjetas_en_el_hilo():
    seen: list[httpx.Request] = []
    card = {"cardId": "p-1", "card": {"sections": []}}
    async with mock_http(b'{"thread": {"name": "spaces/AAA/threads/t1"}}', seen) as http:
        await create_message("spaces/AAA", "", "ya29.token", http, cards = [card], thread = "spaces/AAA/threads/t1")

    body = json.loads(seen[0].content)
    assert body == {"cardsV2": [card], "thread": {"name": "spaces/AAA/threads/t1"}}
    assert seen[0].url.params["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"


def test_e9_un_clic_firmado_va_a_handle_click(monkeypatch):
    seen: list[AddonEvent] = []

    async def fake_verify_token(session, credentials):
        return None

    async def fake_click(session, event):
        seen.append(event)
        return update_message([{"cardId": "p-1", "card": {"sections": []}}])

    async def unexpected(*args, **kwargs):
        raise AssertionError("un clic no va al relay ni al Agente Interno")

    monkeypatch.setattr(google_chat_router, "verify_token", fake_verify_token)
    monkeypatch.setattr(google_chat_router, "handle_click", fake_click)
    monkeypatch.setattr(google_chat_router, "relay_reply", unexpected)
    monkeypatch.setattr(google_chat_router, "answer", unexpected)

    response = TestClient(app).post("/api/v1/google-chat/events", json=click_event(), headers={"Authorization": "Bearer t"})

    assert response.status_code == 200
    assert response.json() == update_message([{"cardId": "p-1", "card": {"sections": []}}])
    assert len(seen) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer token-falso"}])
def test_e10_un_clic_sin_token_valido_responde_401(monkeypatch, headers):
    async def failing_verify(session, credentials):
        raise InvalidGoogleTokenError("token inválido")

    async def unexpected(*args, **kwargs):
        raise AssertionError("no debe llegar a handle_click")

    monkeypatch.setattr(google_chat_router, "verify_token", failing_verify)
    monkeypatch.setattr(google_chat_router, "handle_click", unexpected)

    response = TestClient(app).post("/api/v1/google-chat/events", json=click_event(), headers=headers)

    assert response.status_code == 401


# --- spec 013, U-GC-1 a U-GC-5: nombres de Google Chat validados antes de llamar a la API ---

@pytest.mark.parametrize("name", ["spaces/DM1", "spaces/AAAA_b-1"])
def test_u_gc1_valid_space_acepta_spaces_id(name):
    assert valid_space(name)


@pytest.mark.parametrize("name", [
    "spaces/../x", "spaces/a/b", "spaces/a?x", "spaces/a%2F", "users/x", "", "spaces/", "spaces/a#b", "spaces/a\n"])
def test_u_gc1_valid_space_rechaza_otros_formatos(name):
    assert not valid_space(name)


@pytest.mark.parametrize("name", ["spaces/DM1/attachments/A1", "ClxzcGFjZXMvQUFB+QUFBQ_x-y=="])
def test_u_gc2_valid_attachment_name_acepta_nombres_de_recurso(name):
    assert valid_attachment_name(name)


@pytest.mark.parametrize("name", [
    "/spaces/x", "spaces/../x", "a/./b", "a?b", "a#b", "a%2e", "a b", "a\nb", "", "a//b", "a/", ".."])
def test_u_gc2_valid_attachment_name_rechaza_rutas_relativas_y_escapes(name):
    assert not valid_attachment_name(name)


@pytest.mark.anyio
async def test_u_gc3_create_message_con_space_invalido_no_llama_a_google():
    seen: list[httpx.Request] = []
    async with mock_http(b"{}", seen) as http:
        with pytest.raises(InvalidChatNameError) as error:
            await create_message("spaces/../../v1/spaces/OTRO", "ok", "ya29.token", http)

    assert seen == [] and "OTRO" not in str(error.value)


@pytest.mark.anyio
async def test_u_gc4_download_con_nombre_invalido_no_llama_a_google():
    seen: list[httpx.Request] = []
    async with mock_http(b"x", seen) as http:
        with pytest.raises(InvalidChatNameError) as error:
            await download("spaces/A/../../secreto?alt=json", "ya29.token", http)

    assert seen == [] and "secreto" not in str(error.value)


@pytest.mark.anyio
async def test_u_gc5_create_message_valido_va_solo_a_chat_googleapis():
    seen: list[httpx.Request] = []
    async with mock_http(b'{"thread": {"name": "spaces/DM1/threads/t1"}}', seen) as http:
        thread = await create_message("spaces/DM1", "ok", "ya29.token", http, thread = "spaces/DM1/threads/t1")

    assert thread == "spaces/DM1/threads/t1" and len(seen) == 1
    assert seen[0].url.host == "chat.googleapis.com" and seen[0].url.path == "/v1/spaces/DM1/messages"
    assert seen[0].url.params["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"


# --- spec 014, U-GC-6 y U-GC-7: la barrera re.fullmatch en el sink que reconoce CodeQL (D1) ---

@pytest.mark.anyio
async def test_u_gc6_create_message_en_el_hilo_va_solo_a_chat_googleapis():
    seen: list[httpx.Request] = []
    async with mock_http(b'{"thread": {"name": "spaces/AAA_b-1/threads/t"}}', seen) as http:
        await create_message("spaces/AAA_b-1", "ok", "ya29.token", http, thread = "spaces/AAA_b-1/threads/t")

    assert len(seen) == 1 and seen[0].method == "POST"
    assert str(seen[0].url).startswith("https://chat.googleapis.com/v1/spaces/AAA_b-1/messages?")
    assert seen[0].url.params["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    assert json.loads(seen[0].content)["thread"] == {"name": "spaces/AAA_b-1/threads/t"}


@pytest.mark.anyio
@pytest.mark.parametrize("space", [
    "spaces/../x", "spaces/a/b", "spaces/a?x", "spaces/a%2F", "https://evil.test/x", "spaces/AAA\n", ""])
async def test_u_gc7_create_message_rechaza_el_space_sin_llamar_a_google(space):
    seen: list[httpx.Request] = []
    async with mock_http(b"{}", seen) as http:
        with pytest.raises(InvalidChatNameError) as error:
            await create_message(space, "ok", "ya29.token", http)

    assert seen == []
    assert space == "" or space not in str(error.value)
