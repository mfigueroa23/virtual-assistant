import hashlib
import hmac
import json
import time
import pytest
from fastapi.testclient import TestClient
from main import app
from src.routers import whatsapp as whatsapp_router
from src.services import whatsapp as whatsapp_service
from src.utils.exceptions.database import DatabaseUnavailableError
from src.utils.exceptions.property import PropertyNotFoundError

SECRET = "whsec_test"
URL = "/api/v1/whatsapp/events"
EVENT = {"id": "evt-1", "type": "whatsapp.inbound_message.received", "apiVersion": "v2",
         "whatsappInboundMessage": {"id": "in-1", "from": "+56911111111", "to": "+56951959027", "type": "text",
                                    "text": {"body": "Hola"}}}


def sign(body: bytes, timestamp: int | None = None, secret: str = SECRET) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},s={digest}"


@pytest.fixture
def endpoint(monkeypatch):
    """Endpoint con las properties y handle_event reemplazados: sin BD real ni YCloud."""
    properties = {"ycloud_webhook_secret": SECRET, "whatsapp_enabled": "true"}
    calls: list[str] = []
    graphs: list[object] = []
    state = {"error": None}

    async def fake_get_property(session, key):
        if key not in properties:
            raise PropertyNotFoundError(key)
        return properties[key]

    async def fake_handle_event(graph, session, event):
        if state["error"] is not None:
            raise state["error"]
        calls.append(event.id)
        graphs.append(graph)

    monkeypatch.setattr(whatsapp_router, "get_property", fake_get_property)
    # channel_enabled vive en services y lee whatsapp_enabled con su propio get_property (D14 del plan 008).
    monkeypatch.setattr(whatsapp_service, "get_property", fake_get_property)
    monkeypatch.setattr(whatsapp_router, "handle_event", fake_handle_event)
    monkeypatch.setattr(app.state, "graph", object(), raising = False)
    monkeypatch.setattr(app.state, "whatsapp_graph", object(), raising = False)
    return {"properties": properties, "calls": calls, "graphs": graphs, "state": state}


def post(body: bytes, signature: str | None) -> int:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["YCloud-Signature"] = signature
    return TestClient(app).post(URL, content = body, headers = headers).status_code


def test_e1_evento_firmado_responde_200_y_se_procesa(endpoint):
    body = json.dumps(EVENT).encode()

    assert post(body, sign(body)) == 200
    assert endpoint["calls"] == ["evt-1"]


def test_e6_el_evento_llega_al_grafo_de_whatsapp(endpoint):
    body = json.dumps(EVENT).encode()

    assert post(body, sign(body)) == 200
    assert endpoint["graphs"] == [app.state.whatsapp_graph]
    assert endpoint["graphs"][0] is not app.state.graph


@pytest.mark.parametrize("signature", [
    None,
    "t=123",
    "firma-alterada",
    "vencida",
])
def test_e2_sin_firma_alterada_o_vencida_responde_401(endpoint, signature):
    body = json.dumps(EVENT).encode()
    if signature == "firma-alterada":
        signature = sign(body.replace(b"Hola", b"Chao"))
    elif signature == "vencida":
        signature = sign(body, int(time.time()) - 301)

    assert post(body, signature) == 401
    assert endpoint["calls"] == []


def test_e3_sin_secreto_responde_503(endpoint):
    del endpoint["properties"]["ycloud_webhook_secret"]
    body = json.dumps(EVENT).encode()

    assert post(body, sign(body)) == 503
    assert endpoint["calls"] == []


def test_e4_bd_no_disponible_responde_503(endpoint):
    endpoint["state"]["error"] = DatabaseUnavailableError("sin conexión")
    body = json.dumps(EVENT).encode()

    assert post(body, sign(body)) == 503


@pytest.mark.parametrize("enabled", [None, "false"])
def test_e5_canal_deshabilitado_responde_200_sin_procesar(endpoint, enabled):
    if enabled is None:
        del endpoint["properties"]["whatsapp_enabled"]
    else:
        endpoint["properties"]["whatsapp_enabled"] = enabled
    body = json.dumps(EVENT).encode()

    assert post(body, sign(body)) == 200
    assert endpoint["calls"] == []


def test_cuerpo_firmado_pero_invalido_se_ignora_con_200(endpoint):
    body = b'{"no es": "un evento"}'

    assert post(body, sign(body)) == 200
    assert endpoint["calls"] == []
