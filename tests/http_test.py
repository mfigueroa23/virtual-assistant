from pathlib import Path
from typing import Any
import httpcore
import httpx
import pytest
from src.services.http import outbound_client

URL = "http://api.ycloud.com/v2/whatsapp/messages/sendDirectly"
OK = [b"HTTP/1.1 200 OK\r\n", b"Content-Type: application/json\r\n", b"Content-Length: 11\r\n", b"\r\n", b'{"id":"o1"}']


class FlakyBackend(httpcore.AsyncMockBackend):
    """Red simulada: las primeras `failures` conexiones fallan con ConnectError y registra cada intento."""

    def __init__(self, buffer: list[bytes], failures: int = 0):
        super().__init__(buffer)
        self.failures = failures
        self.attempts: list[dict[str, Any]] = []

    async def connect_tcp(self, host, port, timeout = None, local_address = None, socket_options = None):
        self.attempts.append({"host": host, "local_address": local_address})
        if len(self.attempts) <= self.failures:
            raise httpcore.ConnectError("All connection attempts failed")
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)


def install(client: httpx.AsyncClient, backend: FlakyBackend) -> None:
    # El backend real abre sockets: se reemplaza por el simulado en el pool del transporte.
    transport = client._transport
    assert isinstance(transport, httpx.AsyncHTTPTransport)
    transport._pool._network_backend = backend


def test_u_n1_el_cliente_conecta_desde_ipv4_y_reintenta_dos_veces():
    client = outbound_client(20)
    transport = client._transport

    assert isinstance(transport, httpx.AsyncHTTPTransport)
    assert transport._pool._local_address == "0.0.0.0"
    assert transport._pool._retries == 2
    assert client.timeout == httpx.Timeout(20)


@pytest.mark.anyio
async def test_u_n2_dos_fallos_de_conexion_y_luego_responde():
    backend = FlakyBackend(OK, failures = 2)
    async with outbound_client(20) as http:
        install(http, backend)
        response = await http.post(URL, json = {"to": "+56911111111"})

    assert response.json() == {"id": "o1"}
    assert len(backend.attempts) == 3
    assert all(attempt["local_address"] == "0.0.0.0" for attempt in backend.attempts)


@pytest.mark.anyio
async def test_u_n3_tres_fallos_de_conexion_lanzan_connect_error():
    backend = FlakyBackend(OK, failures = 3)
    async with outbound_client(20) as http:
        install(http, backend)
        with pytest.raises(httpx.ConnectError):
            await http.post(URL, json = {"to": "+56911111111"})

    assert len(backend.attempts) == 3


@pytest.mark.anyio
async def test_u_n5_si_la_peticion_ya_salio_no_se_reintenta():
    # La conexión se establece y el servidor corta sin responder: reenviar podría duplicar el mensaje (RF-18).
    backend = FlakyBackend([])
    async with outbound_client(20) as http:
        install(http, backend)
        with pytest.raises(httpx.RemoteProtocolError):
            await http.post(URL, json = {"to": "+56911111111"})

    assert len(backend.attempts) == 1


def test_u_n4_ningun_modulo_crea_su_propio_cliente():
    # Todo cliente saliente debe pasar por outbound_client para quedar en IPv4 y con reintentos (RF-16).
    src = Path(__file__).parent.parent / "src"
    offenders = [str(path.relative_to(src)) for path in src.rglob("*.py")
                 if path.name != "http.py" and "httpx.AsyncClient(" in path.read_text()]

    assert offenders == []
