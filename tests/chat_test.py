from uuid import uuid4
from fastapi.testclient import TestClient
from main import app
from src.database.session import get_session
from src.routers import chat as chat_router


def test_chat_rechaza_un_chat_id_que_no_es_uuid():
    response = TestClient(app).post("/chat", json={"chat_id": "otro-chat", "mensaje": "Hola"})

    assert response.status_code == 422


def test_u_p3_chat_web_pide_solo_las_areas_externo(monkeypatch):
    audiences: list[tuple[str, ...]] = []

    async def fake_ask(graph, session, coordinator, audiencias, thread_id, mensaje):
        audiences.append(tuple(audiencias))
        return "Hola"

    async def fake_session():
        yield None

    monkeypatch.setattr(chat_router, "ask", fake_ask)
    monkeypatch.setattr(app.state, "graph", object(), raising = False)
    monkeypatch.setitem(app.dependency_overrides, get_session, fake_session)

    response = TestClient(app).post("/chat", json={"chat_id": str(uuid4()), "mensaje": "Hola"})

    assert response.json() == {"respuesta": "Hola"}
    assert audiences == [("externo",)]
