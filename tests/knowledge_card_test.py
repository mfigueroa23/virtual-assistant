import dataclasses
import json
import logging
from types import SimpleNamespace
from typing import Any, cast
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.interfaces.google_chat import AddonEvent
from src.services import knowledge_card
from src.services.faq import to_embedding_text
from src.services.knowledge import Proposal, Resolution
from src.services.knowledge_card import handle_click, knowledge_allowed, proposal_card, turn_cards
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError

URL = "https://chatbot.example/api/v1/google-chat/events"


def proposal(**changes: Any) -> Proposal:
    values: dict[str, Any] = dict(
        id = "p-1", origin = "interno", action = "create", area = "Experiencia", category = "Stack", faq_id = None,
        question = "¿Trabajaste con Kafka?", answer = "Sí, dos años.", previous_question = None, previous_answer = None,
        status = "pending")
    return Proposal(**(values | changes))


def text_of(card: dict[str, Any]) -> str:
    return json.dumps(card, ensure_ascii = False)


def buttons_of(card: dict[str, Any]) -> list[dict[str, Any]]:
    return [button for section in card["card"]["sections"] for widget in section["widgets"]
            for button in widget.get("buttonList", {}).get("buttons", [])]


def params_of(button: dict[str, Any]) -> dict[str, str]:
    return {param["key"]: param["value"] for param in button["onClick"]["action"]["parameters"]}

# --- U-T1 a U-T3: tarjeta de la propuesta ---

def test_u_t1_tarjeta_de_crear_con_confirmar_y_cancelar():
    card = proposal_card(proposal(), URL)

    assert card["cardId"] == "p-1"
    assert "Experiencia › Stack" in text_of(card)
    assert "¿Trabajaste con Kafka?" in text_of(card) and "Sí, dos años." in text_of(card)
    buttons = buttons_of(card)
    assert [button["text"] for button in buttons] == ["Confirmar", "Cancelar"]
    assert all(button["onClick"]["action"]["function"] == URL for button in buttons)
    assert [params_of(button) for button in buttons] == [
        {"proposal": "p-1", "decision": "apply"}, {"proposal": "p-1", "decision": "discard"}]


def test_u_t1_la_propuesta_de_una_consulta_se_guarda_o_se_descarta():
    card = proposal_card(proposal(origin = "consulta"), URL)

    assert [button["text"] for button in buttons_of(card)] == ["Guardar", "Descartar"]


def test_u_t2_tarjeta_de_editar_muestra_la_version_actual_y_la_nueva():
    card = proposal_card(proposal(action = "update", faq_id = 12, previous_question = "¿Trabajaste con Kafka?",
                                  previous_answer = "Sí, dos años.", answer = "Sí, tres años."), URL)

    texto = text_of(card)
    assert "Actual" in texto and "Sí, dos años." in texto
    assert "Nueva" in texto and "Sí, tres años." in texto
    assert texto.index("Sí, dos años.") < texto.index("Sí, tres años.")


def test_u_t2_tarjeta_de_borrar_muestra_la_faq_que_se_borra():
    card = proposal_card(proposal(action = "delete", faq_id = 12, question = None, answer = None,
                                  previous_question = "¿Usas Kafka?", previous_answer = "Sí."), URL)

    texto = text_of(card)
    assert "Se borrará" in texto and "¿Usas Kafka?" in texto
    assert [button["text"] for button in buttons_of(card)] == ["Confirmar", "Cancelar"]


def test_u_t2_tarjeta_de_crear_categoria():
    card = proposal_card(proposal(action = "category", category = "Nube", question = None, answer = None), URL)

    assert "Nube" in text_of(card) and "Experiencia" in text_of(card)


def test_u_t3_el_texto_se_escapa():
    card = proposal_card(proposal(answer = "Uso <b> & Kafka\nen 2024"), URL)

    texto = text_of(card)
    assert "&lt;b&gt; &amp; Kafka<br>en 2024" in texto and "<b> &" not in texto


def test_tarjeta_resuelta_sin_botones_con_la_nota():
    card = proposal_card(proposal(status = "applied"), URL, "Se guardó la pregunta frecuente.")

    assert buttons_of(card) == [] and "Se guardó la pregunta frecuente." in text_of(card)

# --- Acceso y tarjetas del turno (U-K3, U-T4) ---


SESSION = cast(AsyncSession, None)
AVISOS = "spaces/DM1"


def properties(monkeypatch, values: dict[str, str]) -> None:
    async def fake_get_property(session, key):
        if key not in values:
            raise PropertyNotFoundError(key)
        return values[key]

    monkeypatch.setattr(knowledge_card, "get_property", fake_get_property)


@pytest.mark.anyio
@pytest.mark.parametrize(("values", "space", "expected"), [
    ({"whatsapp_notify_space": AVISOS}, AVISOS, True),
    ({"whatsapp_notify_space": f" {AVISOS}\n"}, AVISOS, True),
    ({"whatsapp_notify_space": AVISOS}, "spaces/OTRO", False),
    ({"whatsapp_notify_space": ""}, "", False),
    ({}, AVISOS, False),
])
async def test_u_k3_el_conocimiento_solo_desde_el_space_de_aviso(monkeypatch, values, space, expected):
    properties(monkeypatch, values)

    assert await knowledge_allowed(SESSION, space) is expected


@pytest.mark.anyio
async def test_u_t4_turn_cards_arma_una_tarjeta_por_propuesta_del_turno(monkeypatch):
    properties(monkeypatch, {"google_chat_audience": URL})
    seen: list[str] = []

    async def fake_proposals(session, turn_id):
        seen.append(turn_id)
        return [proposal(id = "p-1"), proposal(id = "p-2", action = "category")]

    monkeypatch.setattr(knowledge_card, "proposals_of_turn", fake_proposals)

    cards = await turn_cards(SESSION, "turno-1")

    assert seen == ["turno-1"] and [card["cardId"] for card in cards] == ["p-1", "p-2"]


@pytest.mark.anyio
async def test_u_t4_turn_cards_sin_propuestas_no_lee_la_audiencia(monkeypatch):
    properties(monkeypatch, {})

    async def fake_proposals(session, turn_id):
        return []

    monkeypatch.setattr(knowledge_card, "proposals_of_turn", fake_proposals)

    assert await turn_cards(SESSION, "turno-1") == []

# --- U-B1 a U-B8: clics en los botones ---

def click(decision: str = "apply", space: str = AVISOS, proposal_id: str = "p-1") -> AddonEvent:
    return AddonEvent.model_validate({
        "commonEventObject": {"parameters": {"proposal": proposal_id, "decision": decision}},
        "chat": {"buttonClickedPayload": {
            "space": {"name": space, "spaceType": "DIRECT_MESSAGE"},
            "message": {"text": "Te propongo esto", "cardsV2": [
                {"cardId": "otra", "card": {"sections": []}}, {"cardId": "p-1", "card": {"sections": []}}]}}}})


class FakeEmbeddings:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.texts: list[str] = []

    async def aembed_documents(self, texts):
        if self.error is not None:
            raise self.error
        self.texts.extend(texts)
        return [[0.1, 0.2] for _ in texts]


class Clicks:
    """Reemplaza propiedades, propuestas y embeddings de handle_click; registra lo que se resuelve."""

    def __init__(self, monkeypatch):
        self.current: Proposal | None = proposal()
        self.outcome = "applied"
        self.resolved: list[tuple[str, str, list[float] | None]] = []
        self.embeddings = FakeEmbeddings()
        self.resolve_error: Exception | None = None
        properties(monkeypatch, {"whatsapp_notify_space": AVISOS, "google_chat_audience": URL})

        async def fake_get(session, proposal_id):
            return self.current

        async def fake_resolve(session, proposal_id, decision, embedding = None):
            if self.resolve_error is not None:
                raise self.resolve_error
            self.resolved.append((proposal_id, decision, embedding))
            assert self.current is not None
            if self.outcome == "resolved":
                return Resolution(self.current, "resolved")
            status = {"applied": "applied", "discarded": "discarded"}.get(self.outcome, "rejected")
            return Resolution(dataclasses.replace(self.current, status = status), cast(Any, self.outcome))

        async def fake_create(session):
            return SimpleNamespace(embeddings = self.embeddings)

        monkeypatch.setattr(knowledge_card, "get_proposal", fake_get)
        monkeypatch.setattr(knowledge_card, "resolve_proposal", fake_resolve)
        monkeypatch.setattr(knowledge_card, "LLM", SimpleNamespace(create = fake_create))


@pytest.fixture
def clicks(monkeypatch) -> Clicks:
    return Clicks(monkeypatch)


def updated_cards(response: dict[str, Any]) -> list[dict[str, Any]]:
    message = response["hostAppDataAction"]["chatDataAction"]["updateMessageAction"]["message"]
    assert message["text"] == "Te propongo esto"
    return message["cardsV2"]


def card_of(response: dict[str, Any]) -> dict[str, Any]:
    cards = updated_cards(response)
    # Solo cambia la tarjeta del botón; las demás del mensaje quedan igual y en su lugar.
    assert [card["cardId"] for card in cards] == ["otra", "p-1"] and cards[0] == {"cardId": "otra", "card": {"sections": []}}
    return cards[1]


@pytest.mark.anyio
async def test_u_b1_confirmar_genera_el_embedding_y_aplica(clicks):
    response = await handle_click(SESSION, click("apply"))

    texto = to_embedding_text("Stack", "¿Trabajaste con Kafka?", "Sí, dos años.")
    assert clicks.embeddings.texts == [texto]
    assert clicks.resolved == [("p-1", "apply", [0.1, 0.2])]
    card = card_of(response)
    assert buttons_of(card) == [] and "guardó" in text_of(card)


@pytest.mark.anyio
@pytest.mark.parametrize(("action", "expected"), [("delete", "borró"), ("category", "creó la categoría")])
async def test_u_b1_borrar_y_crear_categoria_no_generan_embedding(clicks, action, expected):
    clicks.current = proposal(action = action)

    card = card_of(await handle_click(SESSION, click("apply")))

    assert clicks.embeddings.texts == [] and clicks.resolved == [("p-1", "apply", None)]
    assert expected in text_of(card)


@pytest.mark.anyio
async def test_u_b2_cancelar_descarta_sin_embedding(clicks):
    clicks.outcome = "discarded"

    card = card_of(await handle_click(SESSION, click("discard")))

    assert clicks.embeddings.texts == [] and clicks.resolved == [("p-1", "discard", None)]
    assert buttons_of(card) == [] and "Descartada" in text_of(card)


@pytest.mark.anyio
async def test_u_b3_si_falla_el_embedding_no_aplica_y_conserva_los_botones(clicks):
    clicks.embeddings = FakeEmbeddings(RuntimeError("Gemini caído"))

    card = card_of(await handle_click(SESSION, click("apply")))

    assert clicks.resolved == []
    assert [button["text"] for button in buttons_of(card)] == ["Confirmar", "Cancelar"]
    assert "Intenta de nuevo" in text_of(card)


@pytest.mark.anyio
async def test_u_b4_duplicada_queda_sin_botones(clicks):
    clicks.outcome = "duplicate"

    card = card_of(await handle_click(SESSION, click("apply")))

    assert buttons_of(card) == [] and "ya existe" in text_of(card)


@pytest.mark.anyio
async def test_u_b5_faq_cambiada_o_borrada_queda_sin_botones(clicks):
    clicks.current = proposal(action = "update", faq_id = 12, previous_question = "¿P?", previous_answer = "R")
    clicks.outcome = "stale"

    card = card_of(await handle_click(SESSION, click("apply")))

    assert buttons_of(card) == [] and "cambió o se borró" in text_of(card)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["applied", "discarded"])
async def test_u_b6_propuesta_ya_resuelta_muestra_su_estado(clicks, status):
    clicks.current = proposal(status = status)
    clicks.outcome = "resolved"

    card = card_of(await handle_click(SESSION, click("apply")))

    assert clicks.embeddings.texts == []
    assert buttons_of(card) == [] and knowledge_card.ESTADOS[status] in text_of(card)


@pytest.mark.anyio
@pytest.mark.parametrize("values", [{"whatsapp_notify_space": AVISOS}, {}])
async def test_u_b7_clic_fuera_del_space_de_aviso_no_aplica_nada(monkeypatch, clicks, values):
    properties(monkeypatch, values | {"google_chat_audience": URL})

    response = await handle_click(SESSION, click("apply", space = "spaces/OTRO"))

    assert clicks.resolved == [] and clicks.embeddings.texts == []
    message = response["hostAppDataAction"]["chatDataAction"]["createMessageAction"]["message"]
    assert "space de aviso" in message["text"]


@pytest.mark.anyio
async def test_propuesta_inexistente_lo_dice(clicks):
    clicks.current = None

    response = await handle_click(SESSION, click("apply"))

    assert "ya no existe" in response["hostAppDataAction"]["chatDataAction"]["createMessageAction"]["message"]["text"]
    assert clicks.resolved == []


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["", "borrar"])
async def test_clic_sin_parametros_validos_se_ignora(clicks, decision):
    assert await handle_click(SESSION, click(decision)) == {}
    assert clicks.resolved == []


@pytest.mark.anyio
async def test_u_b8_los_fallos_no_dejan_la_pregunta_ni_la_respuesta_en_el_log(clicks, caplog):
    with caplog.at_level(logging.INFO):
        clicks.embeddings = FakeEmbeddings(RuntimeError("¿Trabajaste con Kafka? Sí, dos años."))
        await handle_click(SESSION, click("apply"))
        clicks.embeddings = FakeEmbeddings()
        clicks.resolve_error = DatabaseUnavailableError("¿Trabajaste con Kafka?")
        response = await handle_click(SESSION, click("apply"))

    assert caplog.records
    assert all("Kafka" not in record.getMessage() and "dos años" not in record.getMessage() for record in caplog.records)
    assert [button["text"] for button in buttons_of(card_of(response))] == ["Confirmar", "Cancelar"]

# --- spec 011, U-T1 a U-T10: audiencia en las tarjetas y creación de áreas ---

def area_proposal(**changes: Any) -> Proposal:
    return proposal(**({"action": "area", "area": "Amigos", "category": "", "question": None, "answer": None,
                        "audiencia": "personal", "description": "Lo que saben los amigos"} | changes))


def subtitle_of(card: dict[str, Any]) -> str:
    return card["card"]["header"]["subtitle"]


@pytest.mark.parametrize("audiencia", ["externo", "interno", "personal"])
def test_u_t1_el_subtitulo_lleva_la_audiencia_del_area(audiencia):
    assert subtitle_of(proposal_card(proposal(audiencia = audiencia), URL)) == f"{audiencia} · Experiencia › Stack"


@pytest.mark.anyio
@pytest.mark.parametrize("audiencia", ["interno", "personal"])
async def test_u_t2_confirmar_una_faq_interno_o_personal_aplica_con_embedding(clicks, audiencia):
    clicks.current = proposal(audiencia = audiencia)

    card = card_of(await handle_click(SESSION, click("apply")))

    assert clicks.resolved == [("p-1", "apply", [0.1, 0.2])]
    assert subtitle_of(card) == f"{audiencia} · Experiencia › Stack" and "guardó" in text_of(card)


def test_u_t3_tarjeta_de_area_con_nombre_audiencia_y_descripcion():
    card = proposal_card(area_proposal(description = "Planes <y> juntas"), URL)

    texto = text_of(card)
    assert card["card"]["header"]["title"] == "Nueva área"
    assert subtitle_of(card) == "personal · Amigos"
    assert all(dato in texto for dato in ("Nombre", "Amigos", "Audiencia", "personal", "Descripción", "Planes &lt;y&gt; juntas"))
    assert knowledge_card.QUIEN_VE["personal"] in texto
    assert [button["text"] for button in buttons_of(card)] == ["Confirmar", "Cancelar"]


@pytest.mark.anyio
async def test_u_t4_confirmar_un_area_no_genera_embedding_y_queda_sin_botones(clicks):
    clicks.current = area_proposal()

    card = card_of(await handle_click(SESSION, click("apply")))

    assert clicks.embeddings.texts == [] and clicks.resolved == [("p-1", "apply", None)]
    assert buttons_of(card) == [] and knowledge_card.APLICADO["area"] in text_of(card)


@pytest.mark.anyio
async def test_u_t5_cancelar_un_area_la_descarta_sin_botones(clicks):
    clicks.current = area_proposal()
    clicks.outcome = "discarded"

    card = card_of(await handle_click(SESSION, click("discard")))

    assert clicks.resolved == [("p-1", "discard", None)]
    assert buttons_of(card) == [] and "Descartada" in text_of(card)


@pytest.mark.anyio
async def test_u_t6_area_duplicada_dice_que_ya_existe_un_area_o_un_agente(clicks):
    clicks.current = area_proposal()
    clicks.outcome = "duplicate"

    card = card_of(await handle_click(SESSION, click("apply")))

    assert buttons_of(card) == [] and knowledge_card.DUPLICADO_AREA in text_of(card)
    assert "ya existe un área o un agente con ese nombre" in knowledge_card.DUPLICADO_AREA


@pytest.mark.anyio
async def test_u_t7_segundo_clic_en_un_area_solo_muestra_su_estado(clicks):
    clicks.current = area_proposal(status = "applied")
    clicks.outcome = "resolved"

    card = card_of(await handle_click(SESSION, click("apply")))

    assert buttons_of(card) == [] and knowledge_card.ESTADOS["applied"] in text_of(card)


@pytest.mark.anyio
async def test_u_t7_clic_en_un_area_fuera_del_space_de_aviso_no_crea_nada(clicks):
    clicks.current = area_proposal()

    response = await handle_click(SESSION, click("apply", space = "spaces/OTRO"))

    assert clicks.resolved == []
    assert "space de aviso" in response["hostAppDataAction"]["chatDataAction"]["createMessageAction"]["message"]["text"]


def test_u_t8_la_propuesta_de_una_consulta_muestra_la_audiencia_personal():
    card = proposal_card(proposal(origin = "consulta", area = "Amigos", category = "Planes", audiencia = "personal"), URL)

    assert subtitle_of(card) == "personal · Amigos › Planes"
    assert [button["text"] for button in buttons_of(card)] == ["Guardar", "Descartar"]


@pytest.mark.anyio
async def test_u_t9_una_propuesta_sin_audiencia_se_arma_y_se_resuelve_como_antes(clicks):
    assert subtitle_of(proposal_card(proposal(), URL)) == "Experiencia › Stack"

    card = card_of(await handle_click(SESSION, click("apply")))

    assert clicks.resolved == [("p-1", "apply", [0.1, 0.2])] and subtitle_of(card) == "Experiencia › Stack"


@pytest.mark.anyio
async def test_u_t10_si_falla_crear_un_area_el_log_no_trae_el_nombre_ni_la_descripcion(clicks, caplog):
    clicks.current = area_proposal(area = "Amigos del colegio", description = "Planes con los amigos del colegio")
    clicks.resolve_error = DatabaseUnavailableError("Amigos del colegio: Planes con los amigos del colegio")

    with caplog.at_level(logging.INFO):
        card = card_of(await handle_click(SESSION, click("apply")))

    assert caplog.records
    assert all("colegio" not in record.getMessage() for record in caplog.records)
    assert [button["text"] for button in buttons_of(card)] == ["Confirmar", "Cancelar"]
