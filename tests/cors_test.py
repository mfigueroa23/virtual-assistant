import logging
from typing import Any, cast
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import DefaultClause, String, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from main import app
from src.middlewares import cors as cors_middleware
from src.routers import chat as chat_router
from src.models.cors_origins import CorsOrigin
from src.services.cors import is_origin_allowed
from src.utils.exceptions.database import DatabaseUnavailableError

TABLE = cast(Table, CorsOrigin.__table__)
ALLOWED = "https://app.ejemplo.com"


class FakeResult:
    def __init__(self, value: int | None):
        self.value = value

    def scalar_one_or_none(self) -> int | None:
        return self.value


class FakeSession:
    def __init__(self, value: int | None):
        self.value = value
        self.statements: list[Any] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.value)


class DownSession:
    async def execute(self, statement):
        raise ConnectionRefusedError()


def test_cors_origin_tiene_origen_unico_y_no_nulo():
    unique = [c for c in TABLE.constraints if isinstance(c, UniqueConstraint) and c.name == "uq_cors_origins_origin"]
    origin = TABLE.c.origin

    assert TABLE.name == "cors_origins"
    assert origin.nullable is False
    assert isinstance(origin.type, String) and origin.type.length == 255
    assert len(unique) == 1
    assert [column.name for column in unique[0].columns] == ["origin"]


def test_cors_origin_esta_habilitado_por_defecto_en_la_base_de_datos():
    enabled = TABLE.c.enabled

    assert enabled.nullable is False
    assert isinstance(enabled.server_default, DefaultClause)
    assert str(enabled.server_default.arg) == "true"


@pytest.mark.anyio
async def test_is_origin_allowed_es_true_si_el_origen_esta_registrado_y_habilitado():
    assert await is_origin_allowed(cast(AsyncSession, FakeSession(1)), "https://app.ejemplo.com") is True


@pytest.mark.anyio
async def test_is_origin_allowed_es_false_si_no_hay_fila():
    assert await is_origin_allowed(cast(AsyncSession, FakeSession(None)), "https://otro.ejemplo.com") is False


@pytest.mark.anyio
async def test_is_origin_allowed_filtra_por_el_origen_exacto_y_habilitado():
    session = FakeSession(None)

    await is_origin_allowed(cast(AsyncSession, session), "HTTPS://App.Ejemplo.com:443/")

    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert len(session.statements) == 1
    assert "cors_origins.origin = " in sql
    assert "cors_origins.enabled IS true" in sql
    assert list(compiled.params.values()) == ["HTTPS://App.Ejemplo.com:443/"]


@pytest.mark.anyio
async def test_is_origin_allowed_lanza_database_unavailable_si_la_bd_esta_caida():
    with pytest.raises(DatabaseUnavailableError):
        await is_origin_allowed(cast(AsyncSession, DownSession()), "https://app.ejemplo.com")


class FakeOrigins:
    """Reemplaza is_origin_allowed en el middleware: responde según `allowed` o lanza `error`."""

    def __init__(self, allowed: set[str], error: Exception | None = None):
        self.allowed = allowed
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, session, origin: str) -> bool:
        self.calls.append(origin)
        if self.error is not None:
            raise self.error
        return origin in self.allowed


def fake_origins(monkeypatch, allowed: set[str], error: Exception | None = None) -> FakeOrigins:
    fake = FakeOrigins(allowed, error)
    monkeypatch.setattr(cors_middleware, "is_origin_allowed", fake)
    return fake


def cors_headers(response) -> dict[str, str]:
    return {key: value for key, value in response.headers.items() if key.lower().startswith("access-control-")}


def test_peticion_sin_origin_no_consulta_la_tabla_ni_emite_cabeceras(monkeypatch):
    fake = fake_origins(monkeypatch, {ALLOWED})

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.json() is True
    assert fake.calls == []
    assert cors_headers(response) == {}


def test_origen_no_permitido_se_procesa_sin_cabeceras_cors(monkeypatch):
    fake = fake_origins(monkeypatch, {ALLOWED})

    response = TestClient(app).get("/", headers={"Origin": "https://otro.ejemplo.com"})

    assert response.status_code == 200
    assert response.json() is True
    assert fake.calls == ["https://otro.ejemplo.com"]
    assert cors_headers(response) == {}


@pytest.mark.parametrize("error", [DatabaseUnavailableError("connection refused 10.0.0.5"), RuntimeError("detalle interno")])
def test_error_al_consultar_se_trata_como_no_permitido_y_se_registra(monkeypatch, caplog, error):
    fake_origins(monkeypatch, {ALLOWED}, error)

    with caplog.at_level(logging.ERROR, logger=cors_middleware.__name__):
        response = TestClient(app).get("/", headers={"Origin": ALLOWED})

    assert response.status_code == 200
    assert response.json() is True
    assert cors_headers(response) == {}
    assert any(record.levelno == logging.ERROR and record.name == cors_middleware.__name__ for record in caplog.records)
    assert "10.0.0.5" not in response.text and "detalle interno" not in response.text


def spy_ask(monkeypatch) -> list[str]:
    """Reemplaza ask en el router de chat para comprobar que el preflight no ejecuta el endpoint."""
    calls: list[str] = []

    async def fake_ask(graph, session, coordinator, audiencias, thread_id, mensaje):
        calls.append(mensaje)
        return "ok"

    monkeypatch.setattr(chat_router, "ask", fake_ask)
    return calls


def preflight(path: str, origin: str = ALLOWED, headers: str | None = "content-type, x-algo"):
    request_headers = {"Origin": origin, "Access-Control-Request-Method": "POST"}
    if headers is not None:
        request_headers["Access-Control-Request-Headers"] = headers
    return TestClient(app).options(path, headers=request_headers)


def test_origen_permitido_recibe_su_origen_y_vary_origin(monkeypatch):
    fake_origins(monkeypatch, {ALLOWED})

    response = TestClient(app).get("/", headers={"Origin": ALLOWED})

    assert response.status_code == 200
    assert response.json() is True
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert "Origin" in [value.strip() for value in response.headers["vary"].split(",")]


def test_preflight_permitido_acepta_metodo_y_cabeceras_sin_ejecutar_el_endpoint(monkeypatch):
    fake_origins(monkeypatch, {ALLOWED})
    calls = spy_ask(monkeypatch)

    response = preflight("/chat")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert "POST" in [method.strip() for method in response.headers["access-control-allow-methods"].split(",")]
    assert response.headers["access-control-allow-headers"] == "content-type, x-algo"
    assert calls == []


def test_origen_permitido_nunca_recibe_credenciales_ni_comodin(monkeypatch):
    fake_origins(monkeypatch, {ALLOWED})

    responses = [TestClient(app).get("/", headers={"Origin": ALLOWED}), preflight("/chat")]

    for response in responses:
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["access-control-allow-origin"] != "*"


@pytest.mark.parametrize("variant", [
    "http://app.ejemplo.com",
    "https://app.ejemplo.com:8443",
    "https://sub.app.ejemplo.com",
    "https://app.ejemplo.com/",
    "https://APP.ejemplo.com",
])
def test_variantes_del_origen_registrado_no_reciben_cabeceras(monkeypatch, variant):
    fake = fake_origins(monkeypatch, {ALLOWED})

    response = TestClient(app).get("/", headers={"Origin": variant})

    assert fake.calls == [variant]
    assert cors_headers(response) == {}


def test_deshabilitar_un_origen_aplica_desde_la_siguiente_peticion(monkeypatch):
    fake = fake_origins(monkeypatch, {ALLOWED})
    client = TestClient(app)

    first = client.get("/", headers={"Origin": ALLOWED})
    fake.allowed.clear()
    second = client.get("/", headers={"Origin": ALLOWED})

    assert first.headers["access-control-allow-origin"] == ALLOWED
    assert cors_headers(second) == {}
    assert fake.calls == [ALLOWED, ALLOWED]


@pytest.mark.parametrize("error", [None, DatabaseUnavailableError("connection refused 10.0.0.5")])
def test_preflight_no_permitido_responde_400_en_espanol_sin_cabeceras_cors(monkeypatch, error):
    fake_origins(monkeypatch, {ALLOWED}, error)
    calls = spy_ask(monkeypatch)

    response = preflight("/chat", origin="https://otro.ejemplo.com")

    assert response.status_code == 400
    assert response.text == cors_middleware.ORIGEN_NO_PERMITIDO
    assert cors_headers(response) == {}
    assert "vary" not in response.headers
    assert calls == []


@pytest.mark.parametrize("path", ["/chat", "/api/v1/google-chat/events", "/health"])
def test_cors_aplica_a_todas_las_rutas_de_la_api(monkeypatch, path):
    fake_origins(monkeypatch, {ALLOWED})

    response = preflight(path)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
