# Tareas 008 — Consultas a Marco y respuestas desde Google Chat en WhatsApp

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento escribe primero sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). La versión solo sube en T12.

## Grafo

```
T01 ─┬─► T03 ─► T05 ──────────┬─► T07 ─┐
     ├─► T04 ─────────────────┤        │
     ├─► T06 ─────────────────┘        ├─► T09 ─► T10 ─► T11 ─► T12 ─► T13
     └─► T02 ──────────────────► T08 ──┘
            (T08 también depende de T05 y T06)
```

## Fase 0 — Gate

- [x] **T01 — Cerrar la aprobación del plan y del esquema**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** el usuario aprobó la spec el 2026-10-21. Falta aprobar el plan y el esquema: la tabla
    `whatsapp_notices` y la columna `last_inbound_at`. Pasar el `Status` de `plan.md` a `Approved` y hacer commit de la
    carpeta de la spec en `feature/whatsapp-escalation`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved` y `git log` muestra el commit con la spec, el
    plan y las tareas.

- [x] **T02 — Comprobar los hilos en el mensaje directo con la app**
  - **RF:** RF-13
  - **Depende de:** — (puede ir en paralelo con T01)
  - **Alcance:** paso 1 del orden del plan. Marco responde en el hilo del aviso «[PRUEBA]» publicado en
    `spaces/g4uwxqAAAAE`. Con la 3.6.0 ese mensaje lo contesta el «Agente Interno», en el hilo del evento.
  - **Hecho cuando:** Google Chat deja responder en el hilo del aviso y la respuesta del «Agente Interno» aparece dentro
    de ese hilo. Si no, se detiene la spec y se vuelve a decidir cómo responde Marco (riesgo 1 del plan).

## Fase 1 — Cimientos

- [x] **T03 — Tabla `whatsapp_notices`, columna `last_inbound_at` y migración**
  - **RF:** RF-18, RF-19
  - **Depende de:** T01 (puede ir en paralelo con T04 y T06)
  - **Alcance:**
    - `WhatsappNotice` y `last_inbound_at` en `src/models/whatsapp.py`, con los comentarios del **Data model**.
    - Migración `uv run alembic revision --autogenerate -m "whatsapp notices"`, revisada a mano: solo `create_table`, `add_column` y su reverso.
    - `add_inbound` actualiza `last_inbound_at` con el mayor `sent_at`, también durante la pausa (D4).
    - `FakeStore` de `tests/whatsapp_reply_test.py` con `last_inbound_at`.
  - **Hecho cuando:**
    - U-R22 pasa;
    - `uv run alembic upgrade head`, luego `uv run alembic downgrade -1 && uv run alembic upgrade head`, terminan sin error en la BD local;
    - `uv run alembic check` no reporta diferencias.

- [x] **T04 — `create_message` y `publish_notice` devuelven el hilo**
  - **RF:** RF-18
  - **Depende de:** T01 (puede ir en paralelo con T03 y T06)
  - **Alcance:**
    - `create_message` en `src/services/google_chat.py` devuelve `thread.name` de la respuesta, o `None` si no viene (D2).
    - `publish_notice` devuelve ese hilo en vez de `bool`, y `None` en los casos que hoy dan `False`.
    - `deliver` y `notify` se ajustan al valor nuevo sin cambiar su comportamiento.
  - **Hecho cuando:**
    - U-Q7 pasa;
    - `test_create_message_publica_en_el_space_sin_hilo` comprueba el hilo devuelto;
    - U-D7 a U-D10, `tests/google_chat_reply_test.py` y `tests/whatsapp_reply_test.py` siguen pasando.

- [x] **T05 — Acceso a datos de avisos y respuestas de Marco**
  - **RF:** RF-15, RF-16, RF-18, RF-23
  - **Depende de:** T03
  - **Alcance:**
    - En `src/services/whatsapp.py`: `save_notice`, `find_notice` (con `NoticeTarget`) y `add_marco_reply`, que guarda un `echo` en `history`, levanta la pausa y no sube `epoch` (D5, D6).
    - `channel_enabled` pasa a `src/services/whatsapp.py` y `src/routers/whatsapp.py` la importa (D14).
    - `FakeStore` con las tres funciones nuevas.
    - Script temporal contra la BD local, con limpieza al final.
  - **Hecho cuando:**
    - U-R20 pasa y E1 a E6 siguen pasando;
    - el script contra la BD local guarda y encuentra un aviso, `add_marco_reply` deja `paused_until` nulo sin cambiar `epoch`, y borrar la conversación borra sus avisos.

## Fase 2 — Consultas y respuesta natural

- [x] **T06 — Tool `consultar_a_marco`, guías sin frases y aviso con la línea de respuesta**
  - **RF:** RF-01, RF-02, RF-04, RF-06, RF-09, RF-10, RF-11
  - **Depende de:** T01 (puede ir en paralelo con T03 y T04)
  - **Alcance:**
    - En `src/agents/tools/handoff.py`:
      - `consultar_a_marco(pregunta)` (D10);
      - la docstring de `derivar_a_marco` sin «la información que falta»;
      - `GUIA_DERIVACION`, `GUIA_ENCARGO` y `GUIA_CONSULTA` con el criterio y sin comillas (D11).
    - `WHATSAPP_TOOLS` en `src/agents/coordinator.py`.
    - En `src/services/whatsapp_handoff.py`:
      - `questions_of`;
      - `notice_text(..., questions)` con la sección «Te consulta:» y la línea fija `RESPONDER` (D13).
  - **Hecho cuando:**
    - U-Q1 a U-Q6 pasan con `uv run pytest tests/whatsapp_handoff_test.py tests/whatsapp_reply_test.py`;
    - U-D1 a U-D21 siguen pasando.

- [x] **T07 — Consultas en el canal WhatsApp y hilos guardados**
  - **RF:** RF-03, RF-04, RF-05, RF-06, RF-07, RF-08, RF-18
  - **Depende de:** T04, T05, T06
  - **Alcance:**
    - `answer_batch` en `src/services/whatsapp_reply.py` lee `questions_of(turn)`. Una consulta avisa como un encargo, en el aviso único del turno, y la pausa sigue dependiendo solo de la derivación.
    - `notify` guarda el hilo con `save_notice` cuando se publicó; si no, deja el log con el número y sin el contenido.
    - `FakeHandoff.publish_notice` devuelve un hilo.
  - **Hecho cuando:**
    - U-R16 a U-R19 y U-R21 pasan;
    - U-W1 a U-W27 y U-R1 a U-R15 siguen pasando.

## Fase 3 — Respuestas desde el hilo

- [x] **T08 — Servicio `whatsapp_relay`**
  - **RF:** RF-12, RF-13, RF-14, RF-15, RF-16, RF-17, RF-18, RF-19, RF-20, RF-21, RF-22, RF-23, RF-24
  - **Depende de:** T02, T05, T06
  - **Alcance:**
    - `src/services/whatsapp_relay.py` con `relay_reply` y `compose_reply` (D7, D8):
      - instrucción fija `REDACTAR` y `REDACCION_LIMITE = 15`;
      - textos fijos para canal apagado, sin texto, ventana cerrada (con `wa.me`) y no se pudo enviar;
      - envío con `send_text`/`split_text` y `outbound_client(10)`;
      - log solo con el tipo de error.
    - `family_tone` en `src/services/whatsapp_handoff.py`, usada también por `turn_instructions` sin cambiar su resultado.
  - **Hecho cuando:**
    - U-C1 a U-C11 pasan con `uv run pytest tests/whatsapp_relay_test.py`;
    - U-D11 a U-D20 siguen pasando.

- [x] **T09 — Router de Google Chat**
  - **RF:** RF-12, RF-13
  - **Depende de:** T07, T08
  - **Alcance:** `src/routers/google_chat.py` llama a `relay_reply(request.app.state.whatsapp_graph, session, event)`
    antes de `answer`. Si devuelve texto, responde con `chat_reply` (D9).
  - **Hecho cuando:**
    - E7 y E8 pasan con `uv run pytest tests/google_chat_test.py`, y el resto de ese archivo sigue pasando;
    - la app arranca con `uvicorn` contra la BD local.

## Fase 4 — Documentación, calidad y release

- [x] **T10 — README**
  - **RF:** —
  - **Depende de:** T09
  - **Alcance:**
    - Las consultas en «WhatsApp (YCloud)»: cuándo consulta en vez de derivar y qué trae el aviso.
    - Cómo responder desde el hilo: tono, confirmación, la pausa que se levanta y qué pasa si falla.
    - La ventana de 24 h, y que los avisos anteriores a la 3.7.0 no aceptan respuesta.
    - La tabla `whatsapp_notices` en «Base de datos».
    - El `UPDATE` del prompt del «Agente WhatsApp»: quitar las frases literales y cambiar «sin información → derivar» por «consultar».
  - **Hecho cuando:** `README.md` contiene `consultar_a_marco`, `whatsapp_notices`, «Responde en este hilo», `24 h` y
    el `UPDATE agents` sin las frases «Déjame revisarlo y te cuento» ni «Ahora no puedo, te escribo en un rato».

- [x] **T11 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T10
  - **Alcance:** checklist de `AGENTS.md` sobre toda la rama.
  - **Hecho cuando:**
    - `uv run pytest` pasa completo (incluidos U-Q*, U-R16 a U-R22, U-C* y E7, E8);
    - `uv run pyright` da 0 errores;
    - `uv run alembic check` no reporta diferencias y la BD local está en `head`.

- [ ] **T12 — Versión 3.7.0**
  - **RF:** —
  - **Depende de:** T11
  - **Alcance:** `version = "3.7.0"` en `pyproject.toml` y `uv sync`, en un solo commit con `uv.lock`.
  - **Hecho cuando:** `git log -1` muestra `chore(release): Bump version to v3.7.0` con `pyproject.toml` y `uv.lock`.

- [ ] **T13 — Verificación manual tras el despliegue**
  - **RF:** RF-04, RF-05, RF-09, RF-12, RF-13, RF-14, RF-16, RF-19
  - **Depende de:** T12 y el despliegue en el cluster
  - **Alcance:** seguir la sección **Release** del plan: la migración en la BD remota y el `UPDATE` del prompt, revisado por Marco.
  - **Hecho cuando:**
    - desde otro teléfono, una pregunta que el asistente no sabe recibe una respuesta propia, sin frase hecha, y llega un aviso con la pregunta y «Responde en este hilo…»;
    - el asistente sigue respondiendo otras preguntas del chat;
    - responder en el hilo hace llegar al contacto un mensaje redactado con esa información, y el hilo muestra el texto enviado;
    - responder en el hilo de una derivación levanta la pausa y el asistente vuelve a responder ese chat;
    - un mensaje fuera de los hilos lo responde el «Agente Interno»;
    - tres derivaciones distintas (reunión, precio, urgencia) reciben respuestas redactadas de forma distinta.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T06 |
| RF-02 | T06 |
| RF-03 | T07 |
| RF-04 | T06, T07, T13 |
| RF-05 | T07, T13 |
| RF-06 | T06, T07 |
| RF-07 | T07 |
| RF-08 | T07 |
| RF-09 | T06, T13 |
| RF-10 | T06 |
| RF-11 | T06 |
| RF-12 | T08, T09, T13 |
| RF-13 | T02, T08, T09, T13 |
| RF-14 | T08, T13 |
| RF-15 | T05, T08 |
| RF-16 | T05, T08, T13 |
| RF-17 | T08 |
| RF-18 | T03, T04, T05, T07, T08 |
| RF-19 | T03, T08, T13 |
| RF-20 | T08 |
| RF-21 | T08 |
| RF-22 | T08 |
| RF-23 | T05, T08 |
| RF-24 | T08 |
