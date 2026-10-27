# Tareas 011 — Audiencia personal para WhatsApp y administración de todo el conocimiento

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` = tests de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento escribe primero sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). La versión solo sube en T10.

## Grafo

```
T01 ─┬─► T02 ─► T04 ─┬─► T05 ─► T06 ─┬─► T08 ─► T09 ─► T10 ─► T11
     │               └─► T07 ────────┤
     └─► T03 ─────────────► T05      │
                   (T05 depende de T03 y T04; T08 de T05, T06 y T07; T09 también de T03)
```

## Fase 0 — Gate

- [x] **T01 — Cerrar la aprobación del plan y del esquema**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** el usuario aprobó la spec el 2026-10-26. Faltan tres aprobaciones:
    - el esquema: `ck_areas_audiencia` con `personal`; en `knowledge_proposals`, la acción `area`, las columnas
      `area_name`, `area_description` y `area_audiencia`, `area_id` y `category` nulos, y el check
      `ck_knowledge_proposals_area`;
    - el prompt base fijo por audiencia (D7);
    - quitar `audiencia` de `CoordinatorContext` (D2).

    Después, pasar el `Status` de `plan.md` a `Approved` y hacer commit de la carpeta de la spec en
    `feature/whatsapp-personal`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved` y `git log` muestra el commit con la spec, el
    plan y las tareas.

## Fase 1 — Cimientos

- [x] **T02 — Esquema y migración de la audiencia personal y las propuestas de área**
  - **RF:** RF-01, RF-28
  - **Depende de:** T01
  - **Alcance:** los cambios de **Data model** del plan en `src/models/areas.py` y `src/models/knowledge.py`, con la
    migración `alembic/versions/<rev>_personal_audience.py` generada con
    `uv run alembic revision --autogenerate -m "personal audience"`.
    - Completar a mano el `drop`/`create` de `ck_areas_audiencia`, `ck_knowledge_proposals_action` y
      `ck_knowledge_proposals_area`, que alembic no detecta.
    - `downgrade` borra las propuestas `area` y deja el esquema de la 3.8.1.

    Script SQL en el scratchpad, contra la BD local:
    - `upgrade` con una propuesta de la 3.8.1 ya guardada;
    - `INSERT` de un área `personal`;
    - el check rechaza una propuesta `area` sin nombre y una `create` sin `area_id`;
    - `downgrade` y `upgrade` de nuevo, sin áreas `personal`.
  - **Hecho cuando:** `uv run alembic upgrade head` y `uv run alembic check` pasan sin diferencias en la BD local, el
    script termina sin errores y `uv run pytest` sigue en verde.

- [x] **T03 — Audiencias de los coordinadores y de los sub-agentes**
  - **RF:** RF-02, RF-03, RF-04, RF-05, RF-06, RF-07
  - **Depende de:** T01 (puede ir en paralelo con T02: usa `areas.audiencia`, que ya existe)
  - **Alcance:**
    - `SubAgentConfig.audiencia` y `list_sub_agents(session, audiencias)` en `src/services/agents.py` (D1);
    - `run_turn` y `ask` con `audiencias` en `src/services/assistant.py`;
    - `CoordinatorContext` sin `audiencia` en `src/agents/context.py`, y en `src/agents/tools/delegate.py` el
      `AgentContext` y `is_knowledge` con `sub_agent.audiencia` (D2);
    - `AUDIENCIAS = ("personal", "externo")` en `src/services/whatsapp_reply.py`, sin mirar el trato familiar (D3);
    - `("externo",)` en `src/routers/chat.py` e `("interno",)` en `src/services/google_chat_reply.py`;
    - los fakes de `tests/whatsapp_reply_test.py`, `tests/knowledge_test.py`, `tests/google_chat_reply_test.py` y
      `tests/cors_test.py`.
  - **Hecho cuando:** U-P1 a U-P7 pasan y `uv run pytest` y `uv run pyright` quedan en verde.

## Fase 2 — Conocimiento en tres audiencias

- [ ] **T04 — Servicio y tools del conocimiento en las tres audiencias**
  - **RF:** RF-08, RF-09, RF-10, RF-12, RF-28
  - **Depende de:** T02
  - **Alcance:** en `src/services/knowledge.py` (D4):
    - `AUDIENCIAS`, el parámetro `audiencias` en `list_knowledge`, `search_knowledge` y `create_proposal`;
    - `audiencia` en `AreaSummary`, `KnowledgeHit` y `Proposal`;
    - `outerjoin` con `Area` en `get_proposal`, `proposals_of_turn` y `resolve_proposal`;
    - `external_areas` pasa a ser `areas_text`.

    En `src/agents/tools/knowledge.py`, `ver_conocimiento` agrupa por audiencia y `buscar_conocimiento` muestra la
    audiencia. Volver a correr el script SQL de T02 con `list_knowledge` y `search_knowledge` sobre las tres audiencias.
  - **Hecho cuando:** U-C1 a U-C4 pasan, el script SQL termina sin errores y `uv run pytest` y `uv run pyright` quedan
    en verde.

- [ ] **T05 — Crear áreas con su sub-agente**
  - **RF:** RF-14, RF-15, RF-16, RF-17, RF-19, RF-20, RF-21
  - **Depende de:** T03, T04
  - **Alcance:**
    - en `src/services/knowledge.py`: `create_area_proposal`, la rama `area` de `apply`, que crea el área y
      «Agente \<nombre\>», guarda el `area_id` y da `duplicate` si ya existe el área o el agente (D6, D10), y
      `base_prompt` con `PROMPTS_BASE` (D7);
    - la tool `proponer_area` con la audiencia como `Literal`, en `KNOWLEDGE_TOOLS` (D5).

    Sumar al script SQL una propuesta `area` aplicada, con su área, su agente y el `area_id` guardado, y un nombre
    repetido.
  - **Hecho cuando:** U-N1 a U-N7 pasan, el script SQL termina sin errores y `uv run pytest` y `uv run pyright` quedan
    en verde.

## Fase 3 — Integración

- [ ] **T06 — Tarjetas y clics con audiencia y de creación de área**
  - **RF:** RF-11, RF-13, RF-14, RF-15, RF-18, RF-20, RF-22, RF-25, RF-28, RF-29
  - **Depende de:** T05
  - **Alcance:** en `src/services/knowledge_card.py`:
    - subtítulo `audiencia · área › categoría`;
    - rama `area` de `proposal_card`, con `TITULOS["area"]`, `APLICADO["area"]` y la nota de duplicado de un área;
    - `handle_click` sin embedding para `area`.

    El log de un fallo no lleva el nombre ni la descripción del área.
  - **Hecho cuando:** U-T1 a U-T10 pasan y `uv run pytest` y `uv run pyright` quedan en verde.

- [ ] **T07 — Propuestas desde consultas solo en áreas personal**
  - **RF:** RF-23, RF-24, RF-26, RF-27, RF-29
  - **Depende de:** T04 (puede ir en paralelo con T05 y T06)
  - **Alcance:** en `src/services/faq_learning.py`, `draft_proposal` (D8, D9):
    - lista `list_knowledge(session, ("personal",))` y no llama al LLM sin áreas `personal`;
    - busca las parecidas con `("personal", "externo")`;
    - descarta un área que no sea `personal`;
    - guarda con `audiencias = ("personal",)`.

    `PROPONER` dice que el área es una de las `personal` de la lista.
  - **Hecho cuando:** U-F1 a U-F5 pasan, los U-L* del plan 009 siguen en verde y `uv run pytest` y `uv run pyright`
    quedan en verde.

## Fase 4 — Documentación, calidad y release

- [ ] **T08 — README**
  - **RF:** RF-01, RF-02, RF-06, RF-14, RF-16, RF-23
  - **Depende de:** T05, T06, T07
  - **Alcance:** en `README.md`:
    - la audiencia `personal` y qué ve cada coordinador;
    - que las áreas `personal` las ve cualquier contacto de WhatsApp;
    - la creación de áreas desde «Conocimiento» con su prompt base;
    - que las FAQs aprendidas van a `personal`;
    - la tabla `areas` y `knowledge_proposals` con los valores nuevos;
    - qué hacer antes de un `downgrade` con áreas `personal`.

    Además, los `UPDATE` para Marco: prompt del «Agente Conocimiento» (tres audiencias y `proponer_area`), prompt
    del «Agente Interno» y descripción del área «Conocimiento».
  - **Hecho cuando:** el README menciona `personal` en la descripción de los coordinadores, en la sección de
    conocimiento y en la tabla `areas`, y trae los tres `UPDATE`.

- [ ] **T09 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T03, T08
  - **Alcance:** checklist de `AGENTS.md` sobre la rama completa, y el script SQL de T02, T04 y T05 de punta a punta.
  - **Hecho cuando:** `uv run pytest` pasa, `uv run pyright` termina con 0 errores, `uv run alembic check` no reporta
    diferencias, `uv run alembic current` muestra la revisión nueva en la BD local y el script SQL termina sin
    errores.

- [ ] **T10 — Subir la versión a 3.9.0**
  - **RF:** —
  - **Depende de:** T09
  - **Alcance:** `version = "3.9.0"` en `pyproject.toml` y `uv sync`.
  - **Hecho cuando:** `git log -1` muestra `chore(release): Bump version to v3.9.0` con `pyproject.toml` y `uv.lock`, y
    nada más.

- [ ] **T11 — Verificación manual en producción**
  - **RF:** RF-02, RF-03, RF-07, RF-08, RF-14, RF-17, RF-23
  - **Depende de:** T10, el merge y el despliegue
  - **Alcance:** pasos 3 a 5 de **Release** del plan:
    1. Marco aplica los `UPDATE` del README.
    2. En el space de aviso, pedir a «Conocimiento» un área `personal`: la tarjeta muestra la audiencia y «Confirmar»
       la crea con su sub-agente.
    3. Crear una FAQ en esa área y preguntarla por WhatsApp desde otro teléfono: el asistente la responde.
    4. La misma pregunta en el chat web no la encuentra.
    5. Listar el conocimiento muestra las tres audiencias, y editar una FAQ `interno` pide confirmación y la aplica.
    6. Responder en el hilo de una consulta propone la FAQ en un área `personal`.
    7. Por WhatsApp se siguen respondiendo las preguntas profesionales, dentro de `whatsapp_timeout`.
  - **Hecho cuando:** los siete pasos dan el resultado indicado, y lo que no lo dé queda anotado como tarea nueva.

## RF → tareas
| RF | Tareas |
|---|---|
| RF-01 | T02, T08 |
| RF-02 | T03, T08, T11 |
| RF-03 | T03, T11 |
| RF-04 | T03 |
| RF-05 | T03 |
| RF-06 | T03, T08 |
| RF-07 | T03, T11 |
| RF-08 | T04, T11 |
| RF-09 | T04 |
| RF-10 | T04 |
| RF-11 | T06 |
| RF-12 | T04 |
| RF-13 | T06 |
| RF-14 | T05, T06, T08, T11 |
| RF-15 | T05, T06 |
| RF-16 | T05, T08 |
| RF-17 | T05, T11 |
| RF-18 | T06 |
| RF-19 | T05 |
| RF-20 | T05, T06 |
| RF-21 | T05 |
| RF-22 | T06 |
| RF-23 | T07, T08, T11 |
| RF-24 | T07 |
| RF-25 | T06 |
| RF-26 | T07 |
| RF-27 | T07 |
| RF-28 | T02, T04, T06 |
| RF-29 | T06, T07 |
