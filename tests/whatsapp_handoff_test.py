import asyncio
import logging
from typing import Any, cast
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from src.services import whatsapp_handoff
from src.services.whatsapp_handoff import (
  derivations_of, digits, is_family, notice_text, publish_notice, summarize, tasks_of, transcript, turn_instructions
)
from src.utils.exceptions.property import PropertyNotFoundError

CONTACTO = "+56911111111"
SESSION = cast(Any, None)


def calls(*items: tuple[str, str, str]) -> AIMessage:
    return AIMessage("", tool_calls = [{"name": name, "args": {arg: value}, "id": f"call-{index}"}
                                       for index, (name, arg, value) in enumerate(items)])


# --- Motivos, encargos y transcripción ---

def test_u_d1_derivations_of_junta_los_motivos_sin_repetir():
    turn = [
        calls(("derivar_a_marco", "motivo", "Quiere una reunión"), ("delegar_area", "area", "Experiencia")),
        ToolMessage("Marco recibirá el aviso", tool_call_id = "call-0"),
        calls(("derivar_a_marco", "motivo", "Pregunta por precios"), ("derivar_a_marco", "motivo", "Quiere una reunión")),
        AIMessage("Déjame revisarlo"),
    ]

    assert derivations_of(turn) == ["Quiere una reunión", "Pregunta por precios"]
    assert derivations_of([AIMessage("Hola")]) == []


def test_u_d14_tasks_of_junta_los_encargos_sin_repetir():
    turn = [
        calls(("registrar_encargo", "descripcion", "Enviar el CV"), ("derivar_a_marco", "motivo", "Reunión")),
        calls(("registrar_encargo", "descripcion", "Enviar el portafolio"), ("registrar_encargo", "descripcion", "Enviar el CV")),
    ]

    assert tasks_of(turn) == ["Enviar el CV", "Enviar el portafolio"]


def test_u_d2_transcript_etiqueta_omite_tools_y_toma_los_ultimos_20():
    messages = [HumanMessage(f"pregunta {index}") if index % 2 == 0 else AIMessage(f"respuesta {index}") for index in range(30)]
    messages += [calls(("derivar_a_marco", "motivo", "Reunión")), ToolMessage("guía", tool_call_id = "call-0"),
                 AIMessage("Te escribo pronto")]

    lines = transcript(messages).splitlines()

    assert len(lines) == 20
    assert lines[0] == "Marco: respuesta 11"
    assert lines[-3:] == ["Contacto: pregunta 28", "Marco: respuesta 29", "Marco: Te escribo pronto"]
    assert not any("guía" in line or "Reunión" in line for line in lines)


# --- Resumen ---

class FakeSummarizer:
    def __init__(self, reply: str = "Sara quiere agendar una reunión.", error: Exception | None = None, delay: float = 0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.messages: list[Any] = []

    async def ainvoke(self, messages):
        self.messages = messages
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return AIMessage(self.reply)


@pytest.mark.anyio
async def test_u_d5_summarize_marca_la_conversacion_como_datos():
    llm = FakeSummarizer()

    assert await summarize(cast(Any, llm), "Contacto: ¿Nos juntamos?") == "Sara quiere agendar una reunión."
    system, human = llm.messages
    assert isinstance(system, SystemMessage) and system.text == whatsapp_handoff.RESUMIR
    assert isinstance(human, HumanMessage)
    assert human.text.startswith(whatsapp_handoff.RESUMIR_DATOS) and "Contacto: ¿Nos juntamos?" in human.text


@pytest.mark.anyio
@pytest.mark.parametrize("llm", [FakeSummarizer(error = RuntimeError("caído")), FakeSummarizer(delay = 1)])
async def test_u_d6_summarize_con_error_o_lento_devuelve_none(monkeypatch, llm):
    monkeypatch.setattr(whatsapp_handoff, "RESUMEN_LIMITE", 0.05)

    summary = await summarize(cast(Any, llm), "Contacto: Hola")

    assert summary is None
    assert "No se pudo resumir la conversación" in notice_text("Sara", CONTACTO, ["Reunión"], summary, [], False)


# --- Texto del aviso ---

def test_u_d3_notice_text_trae_nombre_numero_motivos_resumen_y_enlace():
    text = notice_text("Sara", CONTACTO, ["Quiere una reunión", "Pregunta por precios"], "Busca un desarrollador.", [], False)

    for part in ["Sara", CONTACTO, "Quiere una reunión", "Pregunta por precios", "Busca un desarrollador.",
                 "https://wa.me/56911111111"]:
        assert part in text
    assert "wa.me/+" not in text and "Encargos" not in text


def test_u_d4_notice_text_sin_nombre():
    assert "Sin nombre" in notice_text(None, CONTACTO, ["Reunión"], "Resumen", [], False)


def test_u_d13_notice_text_solo_con_encargos():
    text = notice_text("Sara", CONTACTO, [], None, ["Enviar el CV"], False)

    for part in ["Sara", CONTACTO, "Enviar el CV", "https://wa.me/56911111111"]:
        assert part in text
    assert "Quiere hablar contigo" not in text and "Resumen" not in text and "No se pudo resumir" not in text


def test_u_d15_notice_text_con_motivos_y_encargos_en_un_solo_texto():
    text = notice_text("Sara", CONTACTO, ["Reunión"], "Busca un desarrollador.", ["Enviar el CV", "Enviar el portafolio"], False)

    assert text.index("Reunión") < text.index("Busca un desarrollador.") < text.index("Encargos")
    assert "- Enviar el CV\n- Enviar el portafolio" in text


def test_u_d21_notice_text_marca_a_la_familia():
    assert "(familia)" in notice_text("Mamá", CONTACTO, [], None, ["Comprar pan"], True)
    assert "(familia)" not in notice_text("Sara", CONTACTO, [], None, ["Enviar el CV"], False)


# --- Publicación del aviso ---

class FakeChat:
    """Reemplazo de get_property, chat_token y create_message de Google Chat."""

    def __init__(self, monkeypatch, properties: dict[str, str], failures: int = 0, token: str | None = "ya29.token"):
        self.properties = properties
        self.failures = failures
        self.token = token
        self.attempts: list[tuple[str, str]] = []
        monkeypatch.setattr(whatsapp_handoff, "get_property", self.get_property)
        monkeypatch.setattr(whatsapp_handoff, "chat_token", self.chat_token)
        monkeypatch.setattr(whatsapp_handoff, "create_message", self.create_message)
        monkeypatch.setattr(whatsapp_handoff, "ESPERAS", (0.0, 0.0))

    async def get_property(self, session, key):
        if key not in self.properties:
            raise PropertyNotFoundError(key)
        return self.properties[key]

    async def chat_token(self, session, http):
        return self.token

    async def create_message(self, space, text, token, http):
        self.attempts.append((space, text))
        if len(self.attempts) <= self.failures:
            raise httpx.ConnectError("caído")
        return f"{space}/threads/t{len(self.attempts)}"


def test_las_esperas_entre_intentos_son_de_1_y_2_segundos():
    assert whatsapp_handoff.ESPERAS == (1.0, 2.0)


@pytest.mark.anyio
async def test_u_d7_falla_dos_veces_y_publica_al_tercer_intento(monkeypatch):
    chat = FakeChat(monkeypatch, {"whatsapp_notify_space": "spaces/AAA"}, failures = 2)

    assert await publish_notice(SESSION, "aviso") == "spaces/AAA/threads/t3"
    assert chat.attempts == [("spaces/AAA", "aviso")] * 3


@pytest.mark.anyio
async def test_u_q7_publish_notice_devuelve_el_hilo_del_aviso(monkeypatch):
    FakeChat(monkeypatch, {"whatsapp_notify_space": "spaces/AAA"})

    assert await publish_notice(SESSION, "aviso") == "spaces/AAA/threads/t1"


@pytest.mark.anyio
async def test_u_d8_falla_tres_veces_y_no_publica(monkeypatch, caplog):
    chat = FakeChat(monkeypatch, {"whatsapp_notify_space": "spaces/AAA"}, failures = 3)

    with caplog.at_level(logging.WARNING, logger = "src.services.whatsapp_handoff"):
        assert await publish_notice(SESSION, "Sara quiere una reunión") is None

    assert len(chat.attempts) == 3
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert all("Sara" not in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize(("properties", "token"), [({}, "ya29.token"), ({"whatsapp_notify_space": " "}, "ya29.token"),
                                                   ({"whatsapp_notify_space": "spaces/AAA"}, None)])
async def test_u_d9_sin_space_o_sin_token_no_publica(monkeypatch, properties, token):
    chat = FakeChat(monkeypatch, properties, token = token)

    assert await publish_notice(SESSION, "aviso") is None
    assert chat.attempts == []


@pytest.mark.anyio
async def test_u_d10_el_space_se_lee_en_cada_aviso(monkeypatch):
    chat = FakeChat(monkeypatch, {"whatsapp_notify_space": "spaces/AAA"})
    await publish_notice(SESSION, "uno")
    chat.properties["whatsapp_notify_space"] = "spaces/BBB"
    await publish_notice(SESSION, "dos")

    assert chat.attempts == [("spaces/AAA", "uno"), ("spaces/BBB", "dos")]


# --- Instrucciones del turno y círculo familiar ---

def fake_properties(monkeypatch, values: dict[str, str]) -> dict[str, str]:
    async def fake_get_property(session, key):
        if key not in values:
            raise PropertyNotFoundError(key)
        return values[key]

    monkeypatch.setattr(whatsapp_handoff, "get_property", fake_get_property)
    return values


FAMILIA = {"whatsapp_family_phones": "+56 9 2222 2222\n56911111111\n", "whatsapp_family_instructions": "Trátala de mamá.",
           "whatsapp_accepted_tasks": "Enviar mi CV\n\nEnviar mi portafolio"}


def test_u_d16_is_family_compara_solo_los_digitos():
    family = ["+56 9 1111 1111"]

    assert digits("+56 9 1111-1111") == "56911111111"
    assert all(is_family(phone, family) for phone in ["+56 9 1111 1111", "56911111111", "+56911111111"])
    assert not is_family("+56933333333", family)
    assert not is_family("", ["", "abc"])


@pytest.mark.anyio
async def test_u_d11_no_familiar_recibe_los_encargos_aceptables_uno_por_linea(monkeypatch):
    fake_properties(monkeypatch, {"whatsapp_accepted_tasks": "Enviar mi CV\n\n  Enviar mi portafolio  \n"})

    instructions, family = await turn_instructions(SESSION, CONTACTO)

    assert family is False
    assert "registrar_encargo" in instructions
    assert "- Enviar mi CV\n- Enviar mi portafolio" in instructions


@pytest.mark.anyio
@pytest.mark.parametrize("values", [{}, {"whatsapp_accepted_tasks": " \n\n"}])
async def test_u_d12_sin_encargos_aceptables_se_derivan(monkeypatch, values):
    fake_properties(monkeypatch, values)

    instructions, _ = await turn_instructions(SESSION, CONTACTO)

    assert instructions == whatsapp_handoff.SIN_ENCARGOS
    assert "derivar_a_marco" in instructions


@pytest.mark.anyio
async def test_u_d17_familiar_recibe_su_trato_y_cualquier_encargo(monkeypatch):
    fake_properties(monkeypatch, FAMILIA)

    instructions, family = await turn_instructions(SESSION, "+56922222222")

    assert family is True
    assert "Trátala de mamá." in instructions and whatsapp_handoff.CUALQUIER_ENCARGO in instructions
    assert "Enviar mi CV" not in instructions


@pytest.mark.anyio
async def test_u_d18_no_familiar_no_recibe_el_trato_familiar(monkeypatch):
    fake_properties(monkeypatch, FAMILIA)

    instructions, family = await turn_instructions(SESSION, "+56933333333")

    assert family is False
    assert "Trátala de mamá." not in instructions and "- Enviar mi CV" in instructions


@pytest.mark.anyio
async def test_u_d19_las_properties_se_leen_en_cada_turno(monkeypatch):
    values = fake_properties(monkeypatch, dict(FAMILIA))
    first = await turn_instructions(SESSION, CONTACTO)
    values["whatsapp_family_phones"] = "+56922222222"
    values["whatsapp_accepted_tasks"] = "Agendar una llamada"
    second = await turn_instructions(SESSION, CONTACTO)

    assert first[1] is True and second[1] is False
    assert "- Agendar una llamada" in second[0]


@pytest.mark.anyio
@pytest.mark.parametrize("phones", [None, "", "\n \n"])
async def test_u_d20_sin_lista_familiar_nadie_es_familiar(monkeypatch, phones):
    values = {key: value for key, value in FAMILIA.items() if key != "whatsapp_family_phones"}
    if phones is not None:
        values["whatsapp_family_phones"] = phones
    fake_properties(monkeypatch, values)

    instructions, family = await turn_instructions(SESSION, CONTACTO)

    assert family is False and "Trátala de mamá." not in instructions


@pytest.mark.anyio
async def test_aviso_publicado_sin_hilo_cuenta_como_publicado(monkeypatch):
    chat = FakeChat(monkeypatch, {"whatsapp_notify_space": "spaces/AAA"})

    async def without_thread(space, text, token, http):
        chat.attempts.append((space, text))
        return None

    monkeypatch.setattr(whatsapp_handoff, "create_message", without_thread)

    assert await publish_notice(SESSION, "aviso") == ""
    assert len(chat.attempts) == 1
