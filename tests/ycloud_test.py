import hashlib
import hmac
import json
import httpx
import pytest
from src.services import ycloud
from src.services.attachments import MAX_BYTES
from src.services.ycloud import download_media, mark_as_read, send_text, split_text, verify_signature
from src.utils.exceptions.attachments import AttachmentTooLargeError
from src.utils.exceptions.whatsapp import InvalidSignatureError

SECRET = "whsec_test"
BODY = b'{"id":"evt-1","type":"whatsapp.inbound_message.received"}'
NOW = 1_800_000_000


def signature(body: bytes = BODY, timestamp: int = NOW, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},s={digest}"


def test_u_y1_firma_valida():
    verify_signature(signature(), BODY, SECRET, NOW)


def test_u_y2_firma_con_el_cuerpo_alterado():
    with pytest.raises(InvalidSignatureError):
        verify_signature(signature(), BODY.replace(b"evt-1", b"evt-2"), SECRET, NOW)


def test_u_y3_firma_fuera_de_la_tolerancia():
    with pytest.raises(InvalidSignatureError):
        verify_signature(signature(timestamp = NOW - 301), BODY, SECRET, NOW)
    verify_signature(signature(timestamp = NOW - 299), BODY, SECRET, NOW)
    verify_signature(signature(timestamp = NOW + 299), BODY, SECRET, NOW)


@pytest.mark.parametrize("header", [None, "", "s=abc", f"t={NOW}", "t=abc,s=def", "basura"])
def test_u_y4_cabecera_ausente_o_mal_formada(header):
    with pytest.raises(InvalidSignatureError):
        verify_signature(header, BODY, SECRET, NOW)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport = httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_u_y5_send_text_envia_el_mensaje_con_la_api_key():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json = {"id": "out-1"})

    async with client(handler) as http:
        await send_text("key", "+56951959027", "+56911111111", "Hola", http)

    assert len(requests) == 1
    assert str(requests[0].url) == f"{ycloud.YCLOUD_API_URL}/whatsapp/messages/sendDirectly"
    assert requests[0].headers["X-API-Key"] == "key"
    assert json.loads(requests[0].content) == {
        "from": "+56951959027", "to": "+56911111111", "type": "text", "text": {"body": "Hola"}}


@pytest.mark.anyio
async def test_u_y5_send_text_lanza_si_ycloud_rechaza():
    async with client(lambda request: httpx.Response(400, json = {"error": {"code": "invalid"}})) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await send_text("key", "+56951959027", "+56911111111", "Hola", http)


def test_u_y6_split_text_respeta_el_limite_y_los_parrafos():
    parrafos = [("palabra " * 60).strip() for _ in range(25)]
    text = "\n\n".join(parrafos)
    assert len(text) > 9000

    parts = split_text(text)

    assert all(len(part) <= 4096 for part in parts)
    # 8 párrafos de 479 caracteres por mensaje: 8, 8, 8 y 1.
    assert len(parts) == 4
    assert "\n\n".join(parts) == text


def test_u_y7_split_text_sin_saltos_no_parte_palabras():
    words = [f"palabra{i}" for i in range(1500)]
    text = " ".join(words)

    parts = split_text(text, limit = 1000)

    assert all(len(part) <= 1000 for part in parts)
    assert " ".join(parts).split() == words


def test_split_text_corto_queda_igual():
    assert split_text("Hola") == ["Hola"]


@pytest.mark.anyio
async def test_u_y8_mark_as_read_usa_el_id_del_entrante():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async with client(handler) as http:
        await mark_as_read("key", "in-9", http)

    assert requests[0].method == "POST"
    assert str(requests[0].url) == f"{ycloud.YCLOUD_API_URL}/whatsapp/inboundMessages/in-9/markAsRead"
    assert requests[0].headers["X-API-Key"] == "key"


@pytest.mark.anyio
async def test_download_media_devuelve_los_bytes_con_la_api_key():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["X-API-Key"])
        return httpx.Response(200, content = b"datos")

    async with client(handler) as http:
        assert await download_media("key", "https://api.ycloud.com/v2/whatsapp/media/download/m1?sig=x", http) == b"datos"
    assert seen == ["key"]


@pytest.mark.anyio
async def test_u_y9_download_media_corta_al_pasar_el_maximo():
    async with client(lambda request: httpx.Response(200, content = b"x" * (MAX_BYTES + 1))) as http:
        with pytest.raises(AttachmentTooLargeError):
            await download_media("key", "https://api.ycloud.com/v2/whatsapp/media/download/m1", http)
