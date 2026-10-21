# Tareas 007 — Derivación, encargos y círculo familiar en WhatsApp

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento escribe primero sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). La versión solo sube en T11.

## Grafo

```
T01 ─┬─► T02 ───────────────────────────┐
     ├─► T03 ─┬─► T04 ──────────────────┤
     │        └─► T06 ──────────────────┼─► T07 ─► T08 ─► T09 ─► T10 ─► T11 ─► T12
     └─► T05 ───────────────────────────┘
```

## Fase 0 — Gate

- [x] **T01 — Cerrar la aprobación de la spec y el plan**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** el usuario aprobó el 2026-10-20 la spec, el plan, la columna `contact_name` y D10. Después se sumaron
    los encargos (RF-19 a RF-26, D13 a D15) y el círculo familiar (RF-27 a RF-33, D16 y D17), que necesitan su
    aprobación. Pasar el `Status` de `spec.md` y `plan.md` a
    `Approved` y hacer commit de la carpeta de la spec en `feature/whatsapp-handoff`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved` y `git log` muestra el commit con la spec, el
    plan y las tareas.

## Fase 1 — Cimientos

- [x] **T02 — Columna `contact_name` y migración**
  - **RF:** RF-10
  - **Depende de:** T01 (puede ir en paralelo con T03 y T05)
  - **Alcance:** `contact_name: Mapped[str | None]` en `WhatsappConversation` (`src/models/whatsapp.py`), con el
    comentario del **Data model**. Migración `uv run alembic revision --autogenerate -m "whatsapp contact name"`,
    revisada a mano: solo `add_column` y `drop_column`.
  - **Hecho cuando:** `uv run alembic upgrade head`, luego `uv run alembic downgrade -1 && uv run alembic upgrade head`,
    terminan sin error en la BD local; `uv run alembic check` no reporta diferencias y `uv run pyright` da 0 errores.

- [x] **T03 — Cliente HTTP saliente solo IPv4 con reintentos**
  - **RF:** RF-16, RF-17, RF-18
  - **Depende de:** T01 (puede ir en paralelo con T02 y T05)
  - **Alcance:** `outbound_client(timeout)` en `src/services/http.py`, con
    `httpx.AsyncHTTPTransport(local_address = "0.0.0.0", retries = 2)` (D9). En `tests/http_test.py`, un backend
    simulado que envuelve `httpcore.AsyncMockBackend`, falla `connect_tcp` N veces y se instala en el pool del
    transporte.
  - **Hecho cuando:** U-N1, U-N2, U-N3 y U-N5 pasan con `uv run pytest tests/http_test.py`.

- [x] **T04 — Usar `outbound_client` en todos los clientes**
  - **RF:** RF-16, RF-17
  - **Depende de:** T03
  - **Alcance:** reemplazar `httpx.AsyncClient(timeout = …)` por `outbound_client(…)`, con el mismo timeout, en
    `src/routers/google_chat.py` (`verify_token`), `src/services/google_chat_reply.py` (`read_files` y `deliver`) y
    `src/services/whatsapp_reply.py` (`answer_batch`).
  - **Hecho cuando:** U-N4 pasa (`grep` sobre `src/` sin `httpx.AsyncClient(` fuera de `src/services/http.py`), y
    `tests/google_chat_test.py`, `tests/google_chat_reply_test.py` y `tests/whatsapp_reply_test.py` siguen pasando sin
    cambios.

## Fase 2 — Derivación

- [x] **T05 — Tools `derivar_a_marco` y `registrar_encargo`, grafo de WhatsApp y `run_turn`**
  - **RF:** RF-01, RF-02, RF-03, RF-19, RF-20
  - **Depende de:** T01 (puede ir en paralelo con T02 y T03)
  - **Alcance:**
    - En `src/agents/tools/handoff.py`: `derivar_a_marco(motivo)` y `registrar_encargo(descripcion)`, con sus docstrings de cuándo usarlas y una guía como respuesta, sin I/O (D2, D13).
    - En `src/agents/coordinator.py`: `build_coordinator(tools)`, `coordinator_builder` con `[delegar_area]` y `whatsapp_coordinator_builder` con las tres tools (D1). `build_prompt` suma `context.instructions`, el campo nuevo de `CoordinatorContext` (D14).
    - En `src/services/assistant.py`: `run_turn(..., instructions)`, que corta los mensajes del turno desde el `id` propio del `HumanMessage`, y `ask` como envoltorio (D3).
    - U-W10 se reemplaza por U-R1.
  - **Hecho cuando:** U-R1, U-P1, U-H3 y U-H4 pasan, y `tests/chat_test.py`, `tests/google_chat_reply_test.py` y
    U-H1/U-H2 siguen pasando.

- [x] **T06 — Aviso, instrucciones del turno y círculo familiar**
  - **RF:** RF-06, RF-10, RF-11, RF-12, RF-13, RF-14, RF-15, RF-20, RF-21, RF-22, RF-24, RF-25, RF-27, RF-28, RF-29,
    RF-30, RF-31, RF-32, RF-33
  - **Depende de:** T03
  - **Alcance:** en `src/services/whatsapp_handoff.py`:
    - `digits` e `is_family` (D16);
    - `turn_instructions`, que lee en cada turno `whatsapp_family_phones`, `whatsapp_family_instructions` y `whatsapp_accepted_tasks` (D14, D17);
    - `derivations_of` y `tasks_of`;
    - `transcript`, con los últimos 20 mensajes y sin tools;
    - `summarize`, con instrucción fija, frase de datos y `RESUMEN_LIMITE = 20` (D6);
    - `notice_text`, con plantilla fija, «(familia)» si corresponde, secciones de derivación y de encargos en un solo texto y enlace `wa.me` sin `+` (D11, D15);
    - `publish_notice`, que lee `whatsapp_notify_space` en cada llamada y publica con `chat_token` y `create_message`, usando `outbound_client`, en 3 intentos con esperas de 1 y 2 s (D8, D10).
  - **Hecho cuando:** U-D1 a U-D21 pasan con `uv run pytest tests/whatsapp_handoff_test.py`.

- [x] **T07 — Derivación y encargos en el canal WhatsApp**
  - **RF:** RF-03, RF-04, RF-05, RF-07, RF-08, RF-09, RF-10, RF-14, RF-20, RF-23, RF-24, RF-25, RF-26, RF-28, RF-30,
    RF-33
  - **Depende de:** T02, T04, T05, T06
  - **Alcance:**
    - `InboundMessage.customer_profile` en `src/interfaces/whatsapp.py`.
    - En `src/services/whatsapp.py`: `add_inbound` guarda `contact_name`, `Batch.contact_name` y `pause_conversation`, que no sube `epoch` (D4).
    - En `src/services/whatsapp_reply.py`: `handle_event` pasa el nombre. `answer_batch` lee `turn_instructions` con el número del contacto, usa `run_turn`, `derivations_of` y `tasks_of`, y sigue el orden de D5: `epoch` → envío → un solo aviso → pausa solo si hubo derivación y se publicó → marca de leído (D15).
    - `FakeStore` y el fixture `channel` de `tests/whatsapp_reply_test.py` se adaptan (`contact_name`, `pause_conversation`, `run_turn`, aviso falso).
  - **Hecho cuando:** U-R2 a U-R15 pasan, y U-W1 a U-W27 siguen pasando.

## Fase 3 — Integración

- [x] **T08 — Grafo de WhatsApp en `main.py` y en el router**
  - **RF:** RF-01, RF-02
  - **Depende de:** T07
  - **Alcance:** `main.py` compila `app.state.whatsapp_graph` con `whatsapp_coordinator_builder` y el mismo
    checkpointer, y `recover` lo usa. `src/routers/whatsapp.py` pasa `request.app.state.whatsapp_graph` a
    `handle_event`. `/chat` y Google Chat siguen con `app.state.graph`.
  - **Hecho cuando:** E6 pasa con `uv run pytest tests/whatsapp_test.py`, E1 a E5 siguen pasando y la app arranca con
    `uvicorn` contra la BD local (el `lifespan` compila los dos grafos sin error).

## Fase 4 — Documentación, calidad y release

- [x] **T09 — README**
  - **RF:** —
  - **Depende de:** T08
  - **Alcance:**
    - Property `whatsapp_notify_space` y cómo obtenerla desde la URL del mensaje directo con la app (`…/chat/dm/<id>` → `spaces/<id>`).
    - Properties `whatsapp_accepted_tasks`, `whatsapp_family_phones` y `whatsapp_family_instructions`, con ejemplos.
    - La derivación y los encargos en «WhatsApp (YCloud)»: cuándo deriva o acepta, qué trae el aviso, la pausa solo al derivar y qué pasa si el aviso falla.
    - El `UPDATE` del prompt del «Agente WhatsApp» con los criterios de derivación y de encargos.
    - Una nota de conexiones salientes solo IPv4 con reintentos.
  - **Hecho cuando:** `README.md` contiene `whatsapp_notify_space`, `whatsapp_accepted_tasks`, `whatsapp_family_phones`,
    `whatsapp_family_instructions`, `derivar_a_marco`,
    `registrar_encargo`, `wa.me` y el `UPDATE agents`.

- [x] **T10 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T09
  - **Alcance:** checklist de `AGENTS.md` sobre toda la rama.
  - **Hecho cuando:** `uv run pytest` pasa completo (incluidos U-N*, U-D*, U-R*, U-P1, U-H3, U-H4 y E6), `uv run pyright` da
    0 errores, `uv run alembic check` no reporta diferencias y la BD local está en `head`.

- [x] **T11 — Versión 3.6.0**
  - **RF:** —
  - **Depende de:** T10
  - **Alcance:** `version = "3.6.0"` en `pyproject.toml` y `uv sync`, en un solo commit con `uv.lock`.
  - **Hecho cuando:** `git log -1` muestra `chore(release): Bump version to v3.6.0` con `pyproject.toml` y `uv.lock`.

- [ ] **T12 — Verificación manual tras el despliegue**
  - **RF:** RF-04, RF-05, RF-10, RF-11, RF-14, RF-16, RF-22, RF-23, RF-28, RF-29, RF-33
  - **Depende de:** T11 y el despliegue en el cluster
  - **Alcance:** seguir la sección **Release** del plan:
    - migración en la BD remota;
    - `whatsapp_notify_space`, `whatsapp_accepted_tasks`, `whatsapp_family_phones` y `whatsapp_family_instructions`;
    - el `UPDATE` del prompt, revisado por Marco.
  - **Hecho cuando:**
    - desde otro teléfono, pedir una reunión recibe una respuesta del tipo «déjame revisarlo y te escribo»;
    - llega a Google Chat un aviso con nombre, número, motivo, resumen y enlace `wa.me`;
    - el siguiente mensaje del contacto no recibe respuesta;
    - desde otro chat, pedir un encargo de un tipo aceptable recibe «dale, lo hago y te aviso», llega un aviso con qué hacer y quién lo pide, y el asistente sigue respondiendo;
    - un encargo de un tipo no aceptable se deriva, con aviso y pausa;
    - un número de la lista familiar recibe el tono familiar, puede pedir un encargo fuera de la lista y el aviso dice «(familia)»;
    - con `whatsapp_notify_space` vacía no llega aviso y el asistente sigue respondiendo;
    - en el pod, `outbound_client` conecta a `api.ycloud.com` por una IP v4.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T05, T08 |
| RF-02 | T05, T08 |
| RF-03 | T05, T07 |
| RF-04 | T07, T12 |
| RF-05 | T07, T12 |
| RF-06 | T06 |
| RF-07 | T07 |
| RF-08 | T07 |
| RF-09 | T07 |
| RF-10 | T02, T06, T07, T12 |
| RF-11 | T06, T12 |
| RF-12 | T06 |
| RF-13 | T06 |
| RF-14 | T06, T07, T12 |
| RF-15 | T06 |
| RF-16 | T03, T04, T12 |
| RF-17 | T03, T04 |
| RF-18 | T03 |
| RF-19 | T05 |
| RF-20 | T05, T06, T07 |
| RF-21 | T06 |
| RF-22 | T06, T12 |
| RF-23 | T07, T12 |
| RF-24 | T06, T07 |
| RF-25 | T06, T07 |
| RF-26 | T07 |
| RF-27 | T06 |
| RF-28 | T06, T07, T12 |
| RF-29 | T06, T12 |
| RF-30 | T06, T07 |
| RF-31 | T06 |
| RF-32 | T06 |
| RF-33 | T06, T07, T12 |
