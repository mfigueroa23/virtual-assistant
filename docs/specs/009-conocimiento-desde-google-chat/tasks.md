# Tareas 009 — Agente Interno y aprendizaje de preguntas frecuentes desde Google Chat

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento escribe primero sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). La versión solo sube en T13.

## Grafo

```
T01 ─► T03 ─┬─► T04 ─┬─► T06 ─────────┐
            │        └─► T07 ─┬─► T08 ┼─► T11 ─► T12 ─► T13 ─► T14
            └─► T05 ──────────┼─► T10 ┤
T02 ──────────────────────────┴─► T09 ┘
          (T08 depende de T06 y T07; T09 de T02 y T07; T10 de T05 y T07)
```

## Fase 0 — Gate

- [x] **T01 — Cerrar la aprobación del plan y del esquema**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** el usuario aprobó la spec el 2026-10-21. Faltan aprobar el plan y el esquema:
    - la tabla `knowledge_proposals`;
    - la columna `whatsapp_notices.questions`.

    Pasar el `Status` de `plan.md` a `Approved` y hacer commit de la carpeta de la spec en `feature/whatsapp-knowledge`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved` y `git log` muestra el commit con la spec, el
    plan y las tareas.

- [x] **T02 — Comprobar los clics en una tarjeta publicada con la API**
  - **RF:** RF-12, RF-19
  - **Depende de:** — (puede ir en paralelo con T01)
  - **Alcance:** paso 1 del orden del plan.
    1. Con un script en el scratchpad, publicar en `spaces/g4uwxqAAAAE`, con la cuenta de servicio, una tarjeta
       «[PRUEBA] botón» con un botón cuyo `onClick.action.function` sea `google_chat_audience` (D6).
    2. Marco presiona el botón.
    3. Revisar el log del pod de la 3.7.0, que ignora el evento y responde `{}`.
  - **Hecho cuando:** el log del pod muestra `POST /api/v1/google-chat/events` con `200` a la hora del clic. Si no llega
    o llega con `401`, se detiene la spec y se revisa la configuración del complemento (riesgo 1 del plan).

## Fase 1 — Cimientos

- [x] **T03 — Modelo, migración y formato del embedding**
  - **RF:** RF-14, RF-25
  - **Depende de:** T01
  - **Alcance:**
    - `src/models/knowledge.py` con `KnowledgeProposal`, igual que en **Data model** del plan, importado donde Alembic
      lo descubre.
    - `questions` en `WhatsappNotice`.
    - La migración `alembic/versions/<rev>_knowledge_proposals.py`, con un `downgrade` que elimina la columna y la
      tabla.
    - `to_embedding_text` pasa de `src/rag/reindex.py` a `src/services/faq.py`, y `reindex` la importa (D15).
  - **Hecho cuando:**
    - `uv run alembic upgrade head` aplica sobre la BD local;
    - `uv run alembic check` no reporta diferencias;
    - `downgrade -1` seguido de `upgrade head` funciona;
    - `uv run pytest` sigue pasando.

- [x] **T04 — Servicio de conocimiento**
  - **RF:** RF-05 a RF-10, RF-12, RF-13, RF-16 a RF-18
  - **Depende de:** T03
  - **Alcance:** `src/services/knowledge.py`, con `AreaSummary`, `KnowledgeHit`, `Proposal` y estas funciones:
    - `list_knowledge`;
    - `search_knowledge`, con `top_k` y `max_distance`, en todas las áreas `externo` y con el id de cada FAQ;
    - `create_proposal`, que valida que el área sea `externo` y que la FAQ exista, y copia la versión actual en
      `previous_*`;
    - `proposals_of_turn`;
    - `resolve_proposal`, que hace `FOR UPDATE` y solo actúa si la propuesta está `pending` (D9). Aplica cada
      acción:
      - crea la FAQ con su embedding y `status = 'index'`, creando la categoría si no existe;
      - edita con el `WHERE` de D8;
      - borra;
      - crea la categoría.

      Si la FAQ está duplicada o cambió, la deja `rejected`.

    Los errores de SQL se envuelven en `DatabaseUnavailableError`.
  - **Hecho cuando:**
    - un script en el scratchpad contra la BD local (`grep -q '^DB_HOST=localhost' .env`) comprueba cada caso:
      - listar;
      - buscar con id;
      - proponer en un área `interno`, que se rechaza;
      - aplicar cada acción;
      - duplicada;
      - FAQ editada antes de confirmar;
      - FAQ borrada antes de confirmar;
      - categoría nueva;
      - resolver dos veces;
    - `uv run pyright` da 0 errores.

- [x] **T05 — Preguntas guardadas en el aviso**
  - **RF:** RF-20, RF-25
  - **Depende de:** T03 (puede ir en paralelo con T04)
  - **Alcance:**
    - `save_notice(session, thread, phone, questions)` guarda las preguntas una por línea, o `NULL` si no hay (D10).
    - `NoticeTarget.questions` y `find_notice` las devuelven.
    - `notify`, en `whatsapp_reply.py`, pasa las preguntas del turno.
    - Se ajustan `FakeStore` y los tests de la spec 008 que llaman a `save_notice`.
  - **Hecho cuando:**
    - U-L8 pasa con `uv run pytest tests/whatsapp_reply_test.py`, y U-R16 a U-R22 siguen pasando;
    - el script de T04 comprueba `save_notice`/`find_notice` con preguntas y sin ellas contra la BD local.

## Fase 2 — Sub-agente de conocimiento

- [x] **T06 — Grafo, tools y exclusión del sub-agente de conocimiento**
  - **RF:** RF-01 a RF-10
  - **Depende de:** T04
  - **Alcance:**
    - `turn_id` en `AgentContext` y `CoordinatorContext`.
    - `build_sub_agent(tools)`, con `sub_agent_graph` y `knowledge_graph` (D4).
    - `src/agents/tools/knowledge.py` con `ver_conocimiento`, `buscar_conocimiento`, `proponer_cambio` y
      `GUIA_PROPUESTA`.
    - `delegar_area` elige el grafo por `CONOCIMIENTO` y pasa el `turn_id` (D3).
    - `run_turn(..., turn_id=None, exclude=())` excluye áreas. Solo lanza `AgentNotFoundError` si la audiencia no
      tiene sub-agentes antes de excluir (D5).
  - **Hecho cuando:**
    - U-K1, U-K2 y U-K4 a U-K8 pasan con `uv run pytest tests/knowledge_test.py`;
    - también pasa la parte de U-K3 que prueba `run_turn` con `exclude`;
    - `uv run pytest tests/chat_test.py` sigue pasando.

## Fase 3 — Tarjetas y clics

- [x] **T07 — Interfaces de tarjetas, `create_message` y `proposal_card`**
  - **RF:** RF-07 a RF-09, RF-11, RF-21
  - **Depende de:** T04 (puede ir en paralelo con T05 y T06)
  - **Alcance:**
    - En `src/interfaces/google_chat.py`:
      - `button_clicked_payload` y `common_event_object.parameters`;
      - `chat_reply(text, cards=())`;
      - `update_message(cards)`.
    - `create_message(..., cards=(), thread=None)`. Con hilo envía `thread.name` y
      `messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`.
    - `src/services/knowledge_card.py` con `proposal_card`, que lleva botones según el origen y el estado, y escapa
      el texto (D13).
  - **Hecho cuando:**
    - U-T1 a U-T3 pasan con `uv run pytest tests/knowledge_card_test.py`;
    - E11 pasa con `uv run pytest tests/google_chat_test.py`;
    - el test existente de `create_message` sigue pasando.

- [x] **T08 — Tarjetas en las respuestas del Agente Interno**
  - **RF:** RF-03, RF-04, RF-07, RF-11
  - **Depende de:** T06, T07
  - **Alcance:** en `src/services/google_chat_reply.py`:
    - `process` devuelve `ChatAnswer(text, cards)`, genera el `turn_id` y excluye `CONOCIMIENTO` fuera de
      `whatsapp_notify_space` o si la property falta.
    - Tras el turno, `process` arma una tarjeta por cada propuesta de `proposals_of_turn`.
    - `answer` responde `chat_reply(text, cards)`.
    - `deliver` pasa las tarjetas a `create_message`.
  - **Hecho cuando:**
    - U-T4 y U-K3 completo pasan;
    - `uv run pytest tests/google_chat_reply_test.py` sigue pasando con el texto en `ChatAnswer`.

- [x] **T09 — Clics en los botones**
  - **RF:** RF-12 a RF-19, RF-27
  - **Depende de:** T02, T07
  - **Alcance:**
    - `handle_click(session, event)` en `src/services/knowledge_card.py`:
      1. Comprueba el space de aviso.
      2. Con `apply`, genera el embedding antes de la transacción (D7).
      3. Llama a `resolve_proposal`.
      4. Devuelve `update_message` con la tarjeta y la nota de resultado.

      El log de un fallo guarda solo el tipo de error.
    - `src/routers/google_chat.py` entrega a `handle_click` los eventos con `button_clicked_payload`, antes que el
      relay y `answer`.
  - **Hecho cuando:**
    - U-B1 a U-B8 pasan con `uv run pytest tests/knowledge_card_test.py`;
    - E9 y E10 pasan con `uv run pytest tests/google_chat_test.py`, y E1 a E8 siguen pasando.

## Fase 4 — Propuestas desde la consulta

- [x] **T10 — Proponer una FAQ tras responder en el hilo**
  - **RF:** RF-20 a RF-27
  - **Depende de:** T05, T07
  - **Alcance:** `src/services/faq_learning.py` con:
    - `FaqDraft` y el prompt fijo `PROPONER`;
    - `schedule_proposal` y `cancel_pending`, con el mismo patrón de `pending` que `google_chat_reply`;
    - `propose_faq`:
      - usa `search_knowledge`, `list_knowledge` y `with_structured_output`;
      - descarta el borrador con `reusable = False`, con `already_answered = True`, con un área fuera de la lista o
        con datos del contacto (D14);
      - guarda la propuesta con `origin = "consulta"` y la publica con `create_message(..., thread)`;
      - corre dentro de `PROPUESTA_LIMITE`.

    `relay_reply` llama a `schedule_proposal` después de `add_marco_reply`, solo si el aviso tiene preguntas (D11), y
    `main.py` cancela las propuestas en curso al apagar.
  - **Hecho cuando:**
    - U-L1 a U-L7 pasan con `uv run pytest tests/faq_learning_test.py`;
    - U-C1 a U-C11 siguen pasando con `uv run pytest tests/whatsapp_relay_test.py`.

## Fase 5 — Documentación, calidad y release

- [ ] **T11 — README**
  - **RF:** —
  - **Depende de:** T08, T09, T10
  - **Alcance:**
    - El «Agente Interno» y el sub-agente de conocimiento: qué puede hacer y que solo funciona en el space de aviso.
    - Las tarjetas con «Confirmar»/«Cancelar».
    - Las propuestas desde el hilo con «Guardar»/«Descartar», y que los avisos anteriores a la 3.8.0 no generan
      propuestas.
    - La tabla `knowledge_proposals` y la columna `questions` en «Base de datos».
    - Los `INSERT` del «Agente Interno», del área `interno` «Conocimiento» y del «Agente Conocimiento», con prompts
      que piden confirmar siempre con la tarjeta y no decir que un cambio está hecho.
  - **Hecho cuando:** `README.md` contiene `knowledge_proposals`, `Conocimiento`, `proponer_cambio`, «Confirmar»,
    «Guardar» y el `INSERT INTO agents` del «Agente Interno».

- [ ] **T12 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T11
  - **Alcance:** checklist de `AGENTS.md` sobre toda la rama.
  - **Hecho cuando:**
    - `uv run pytest` pasa completo, incluidos U-K*, U-T*, U-B*, U-L* y E9 a E11;
    - `uv run pyright` da 0 errores;
    - `uv run alembic check` no reporta diferencias y la BD local está en `head`.

- [ ] **T13 — Versión 3.8.0**
  - **RF:** —
  - **Depende de:** T12
  - **Alcance:** `version = "3.8.0"` en `pyproject.toml` y `uv sync`, en un solo commit con `uv.lock`.
  - **Hecho cuando:** `git log -1` muestra `chore(release): Bump version to v3.8.0` con `pyproject.toml` y `uv.lock`.

- [ ] **T14 — Verificación manual tras el despliegue**
  - **RF:** RF-01, RF-03, RF-05, RF-07, RF-12 a RF-14, RF-20, RF-21
  - **Depende de:** T13 y el despliegue en el cluster
  - **Alcance:** seguir la sección **Release** del plan: la migración en la BD remota y los `INSERT` del README,
    revisados por Marco.
  - **Hecho cuando:**
    - un mensaje en el space de aviso fuera de los hilos lo responde el «Agente Interno» y no «Servicio no
      disponible»;
    - pedirle que liste el conocimiento muestra las áreas `externo` con sus categorías;
    - pedirle crear una FAQ muestra una tarjeta, y «Confirmar» la guarda y deja la tarjeta sin botones;
    - el «Agente WhatsApp» usa esa FAQ en la siguiente pregunta desde otro teléfono;
    - editar y borrar una FAQ piden confirmación, y «Cancelar» no cambia nada;
    - responder en el hilo de una consulta envía la respuesta al contacto y publica una propuesta;
    - «Guardar» deja la propuesta disponible para la siguiente pregunta igual;
    - en otro space, el «Agente Interno» no puede ver ni cambiar el conocimiento.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T06, T14 |
| RF-02 | T06 |
| RF-03 | T06, T08, T14 |
| RF-04 | T06, T08 |
| RF-05 | T04, T06, T14 |
| RF-06 | T04, T06 |
| RF-07 | T04, T06, T07, T08, T14 |
| RF-08 | T04, T06, T07 |
| RF-09 | T04, T06, T07 |
| RF-10 | T04, T06 |
| RF-11 | T07, T08 |
| RF-12 | T02, T04, T09, T14 |
| RF-13 | T04, T09, T14 |
| RF-14 | T03, T09, T14 |
| RF-15 | T09 |
| RF-16 | T04, T09 |
| RF-17 | T04, T09 |
| RF-18 | T04, T09 |
| RF-19 | T02, T09 |
| RF-20 | T05, T10, T14 |
| RF-21 | T07, T10, T14 |
| RF-22 | T10 |
| RF-23 | T10 |
| RF-24 | T10 |
| RF-25 | T03, T05, T10 |
| RF-26 | T10 |
| RF-27 | T09, T10 |
