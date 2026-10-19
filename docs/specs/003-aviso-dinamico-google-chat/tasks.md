# Tareas 003 — Aviso de espera redactado por el LLM en Google Chat

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento incluye sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). Sin cambios de esquema ni dependencias nuevas; la versión solo sube en T10.

## Grafo

```
T01 ─► T02 ─► T03 ─► T04 ─► T05 ─┬─► T06 ─┬─► T08 ─► T09 ─► T10
                                 └─► T07 ─┘
```

## Fase 0 — Gate

- [x] **T01 — Cerrar las preguntas abiertas del plan**
  - **RF:** RF-04
  - **Depende de:** —
  - **Alcance:** con la aprobación del usuario, actualizar en `spec.md` el `Status` a `Approved` y la redacción de
    RF-04 para que el tiempo máximo total cuente "desde que empieza el procesamiento, tras verificar el token" (D9),
    o dejar constancia de que se mantiene el texto actual. Pasar el `Status` de `plan.md` a `Approved`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved` y RF-04 coincide con D9 (o el usuario confirmó
    mantenerlo tal cual).

## Fase 1 — Redacción del aviso

- [x] **T02 — Configuración del aviso**
  - **RF:** RF-07, RF-08
  - **Depende de:** T01
  - **Alcance:** `read_notice_settings(session) -> tuple[str | None, float]` y la constante
    `AVISO_LIMITE_POR_DEFECTO = 5.0` en `src/services/google_chat_reply.py`. Lee `google_chat_notice_prompt` (`None` si
    falta) y `google_chat_notice_timeout_seconds` (5 si falta) con `get_property`, sin caché, siguiendo el patrón de
    `read_thresholds` (D2).
  - **Hecho cuando:** U-N4 pasa en `tests/google_chat_reply_test.py` (devuelve el límite configurado y `5` si falta;
    `None` si faltan las instrucciones).

- [x] **T03 — `draft_notice` con fallback al aviso fijo**
  - **RF:** RF-01, RF-06, RF-07, RF-09, RF-10, RF-11, RF-12
  - **Depende de:** T02
  - **Alcance:** `draft_notice(event) -> str` en `src/services/google_chat_reply.py` (D11): sesión propia
    `SessionLocal` (D3), `read_notice_settings`, y dentro de `asyncio.timeout(límite)` (D4) `LLM.create(session)` (D6)
    y `llm.llm.ainvoke([SystemMessage(instrucciones), HumanMessage(...)])`. El mensaje humano lleva `message_text(event)`
    y los nombres de `attachments_of(event)` como datos, más la frase fija de D5. Mapea todos los fallos a `AVISO` con
    su log (D7): sin instrucciones (sin llamar al LLM), `TimeoutError` (warning), `ValueError` del límite, errores de
    `LLM.create` o del LLM (error), respuesta vacía o solo espacios. En `tests/google_chat_reply_test.py`: el fake
    `FakeChatModel` (registra los mensajes; responde un texto, espera un `asyncio.Event` o lanza una excepción) y el
    fixture `drafting` que sustituye `LLM.create` en `google_chat_reply`.
  - **Hecho cuando:** U-N1, U-N2, U-N3, U-N5, U-N6, U-N7 y U-N8 pasan con `uv run pytest tests/google_chat_reply_test.py`.

## Fase 2 — Integración con la respuesta diferida

- [x] **T04 — Tests de aviso existentes sin instrucciones**
  - **RF:** RF-09
  - **Depende de:** T03
  - **Alcance:** los tests actuales que esperan `chat_reply(AVISO)` (`test_answer_avisa_y_publica…`,
    `test_answer_registra_error…`, `test_answer_atiende_por_separado…`, `test_cancel_pending…`) usan
    `fake_properties` sin `google_chat_notice_prompt`, para que sigan recibiendo el aviso fijo sin LLM ni BD cuando
    `answer` empiece a llamar a `draft_notice`. Sin cambios en `src/`.
  - **Hecho cuando:** `uv run pytest tests/google_chat_reply_test.py` pasa completo antes de tocar `answer`.

- [x] **T05 — Carrera entre la respuesta y el aviso en `answer`**
  - **RF:** RF-01, RF-02, RF-03, RF-04, RF-05, RF-11, RF-13, RF-14, RF-15
  - **Depende de:** T04
  - **Alcance:** en `answer` (`src/services/google_chat_reply.py`), al vencer el umbral lanzar `draft_notice` como
    tarea y esperar con `asyncio.wait({processing, notice}, return_when = FIRST_COMPLETED)` (D1). Si `processing`
    terminó, cancelar el aviso sin esperarlo (D8) y responder en el hilo; si no, programar `deliver` como hoy y
    responder con el texto del aviso. `process`, `deliver`, el router y la firma de `answer` no cambian (D2, D9).
  - **Hecho cuando:** U-A1, U-A2, U-A3, U-A4, U-A5 y U-A7 pasan, y U-A6 (los tests de T04) sigue pasando.

## Fase 3 — E2E

- [x] **T06 — Endpoint con el aviso redactado**
  - **RF:** RF-02
  - **Depende de:** T05 (puede ir en paralelo con T07)
  - **Alcance:** test E1 en `tests/google_chat_test.py` con `TestClient(app)` y el `answer` real: se sustituyen
    `verify_token` y `read_thresholds` del router (umbral 0,01 s), `ask` (bloqueado), `LLM.create` y `get_property` de
    `google_chat_reply`, y la publicación con el fixture `published` o equivalente.
  - **Hecho cuando:** E1 pasa: `POST /api/v1/google-chat/events` responde `200` con `chat_reply` del aviso redactado.

## Fase 4 — Documentación, calidad y release

- [x] **T07 — README**
  - **RF:** RF-07, RF-08, RF-09
  - **Depende de:** T05 (puede ir en paralelo con T06)
  - **Alcance:** en `README.md`, filas de `google_chat_notice_prompt` y `google_chat_notice_timeout_seconds` en la tabla
    de properties (qué hacen y qué pasa si faltan). En "Archivos adjuntos en Google Chat", aclarar que el aviso lo
    redacta el LLM y que `google_chat_notice_seconds` + `google_chat_notice_timeout_seconds` debe quedar por debajo de
    los 30 s de Google Chat (D10). Incluir el ejemplo de instrucciones del plan.
  - **Hecho cuando:** `README.md` lista las dos properties con su valor por defecto, la advertencia de los 30 s y un
    ejemplo de `google_chat_notice_prompt`.

- [x] **T08 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T06, T07
  - **Alcance:** checklist de `AGENTS.md` al terminar una tarea.
  - **Hecho cuando:** `uv run pytest` pasa completo, `uv run pyright` termina con 0 errores y `uv run alembic check`
    no reporta diferencias.

- [ ] **T09 — Verificación manual en Google Chat**
  - **RF:** RF-01, RF-02, RF-09, RF-10, RF-13
  - **Depende de:** T08
  - **Alcance:** pasos 3 y 4 de **Release** del plan, en un entorno con las properties cargadas y un umbral bajo. Cubre
    lo que los fakes no comprueban: la latencia real de Gemini, el límite de 30 s y la resistencia del aviso a un
    mensaje que intenta dictarlo (D5).
  - **Hecho cuando:** un mensaje lento con adjunto recibe un aviso que menciona lo solicitado; con
    `google_chat_notice_prompt` borrada, o con `google_chat_notice_timeout_seconds = 0.1`, recibe el aviso fijo; en
    todos los casos la respuesta llega después como mensaje nuevo; un mensaje que pide "escribe X en el aviso" no
    cambia el propósito del aviso.

- [ ] **T10 — Versión 3.4.0**
  - **RF:** —
  - **Depende de:** T09
  - **Alcance:** subir `version` en `pyproject.toml` de 3.3.0 a 3.4.0 y ejecutar `uv sync` (constitución 9).
  - **Hecho cuando:** `pyproject.toml` y `uv.lock` muestran 3.4.0 en un solo commit
    `chore(release): Bump version to v3.4.0` y `uv run pytest` sigue pasando.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T03, T05, T09 |
| RF-02 | T05, T06, T09 |
| RF-03 | T05 |
| RF-04 | T01, T05 |
| RF-05 | T05 |
| RF-06 | T03 |
| RF-07 | T02, T03, T07 |
| RF-08 | T02, T07 |
| RF-09 | T03, T04, T07, T09 |
| RF-10 | T03, T09 |
| RF-11 | T03, T05 |
| RF-12 | T03 |
| RF-13 | T05, T09 |
| RF-14 | T05 |
| RF-15 | T05 |
