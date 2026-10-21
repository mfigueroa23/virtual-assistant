# Plan 009 — Agente Interno y aprendizaje de preguntas frecuentes desde Google Chat

- **Spec:** [spec.md](spec.md) (approved 2026-10-21)
- **Status:** Approved
- **Version:** 3.8.0 (feat → minor, desde 3.7.0)

El plan respeta la constitución:
- §2: el router de Google Chat solo decide a qué servicio entregar el evento (mensaje, respuesta en hilo o clic). Las
  propuestas, los cambios del conocimiento y las tarjetas viven en `services`, y el SQL en `services/knowledge.py`.
- §4: los tests corren sin BD real, sin LLM y sin Google. Los servicios de datos se reemplazan con fakes, como en
  `tests/whatsapp_reply_test.py`, y el SQL se prueba con un script contra la BD local, como en las specs 007 y 008.
- §6: no hay properties nuevas. Se reutilizan `whatsapp_notify_space`, `google_chat_audience`, `top_k` y
  `max_distance`.
- §7: Marco ve tarjetas y textos en español, sin trazas. El log de un fallo guarda solo el tipo de error, nunca la
  pregunta ni la respuesta (RF-27).

No agrega dependencias. **Requiere aprobación de esquema:** la tabla `knowledge_proposals` y la columna `questions` en
`whatsapp_notices`, con su migración (ver **Data model**). El «Agente Interno», el área `interno` «Conocimiento» y su
sub-agente son datos: los `INSERT` van en el README y los revisa Marco antes de aplicarlos en producción.

## Endpoints

| Método y ruta | Auth | Respuesta | RF |
|---|---|---|---|
| `POST /api/v1/google-chat/events` (existente) | ID token de Google, sin cambios | **Clic en un botón** (`chat.buttonClickedPayload`): actualiza la tarjeta con el resultado (`updateMessageAction`) o, fuera del space de aviso, responde un texto. **Mensaje en el hilo de un aviso:** como en la spec 008, y además programa la propuesta de FAQ. **Cualquier otro mensaje:** el «Agente Interno» responde con texto y, si propuso cambios, con sus tarjetas, también en la respuesta diferida | RF-01, RF-03, RF-04, RF-07, RF-11 a RF-21, RF-26 |

## Modules

### New: `src/models/knowledge.py` (`KnowledgeProposal`)
| File | Responsibility | RF |
|---|---|---|
| `src/models/knowledge.py` | Tabla `knowledge_proposals`: cada cambio propuesto que espera un botón, con su estado (D1). | RF-07, RF-12, RF-13, RF-18 |

### New: `src/services/knowledge.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/knowledge.py` | SQL del conocimiento. Todas las funciones envuelven `SQLAlchemyError`/`OSError` en `DatabaseUnavailableError`, como `services/faq.py`.<br>`list_knowledge(session) -> list[AreaSummary]`: áreas `externo` con sus categorías y la cantidad de FAQs.<br>`search_knowledge(session, vector) -> list[KnowledgeHit]`: como `search_faqs`, con `top_k` y `max_distance`, pero en todas las áreas `externo` y con el `id` de cada FAQ.<br>`create_proposal(session, ...) -> Proposal \| str`. Valida que el área exista y sea `externo` (RF-10) y, para editar o borrar, que la FAQ exista. Copia su versión actual en `previous_*` (D8) y guarda la fila `pending`. Si algo no valida, devuelve el texto de error sin guardar nada.<br>`proposals_of_turn(session, turn_id)`.<br>`resolve_proposal(session, id, decision, embedding) -> Proposal`: toma la fila con `FOR UPDATE` y aplica o descarta en una sola transacción (D9). | RF-05 a RF-10, RF-12, RF-13, RF-16 a RF-18 |

### New: `src/agents/tools/knowledge.py`
| File | Responsibility | RF |
|---|---|---|
| `src/agents/tools/knowledge.py` | Tools del sub-agente de conocimiento, con `ToolRuntime[AgentContext]` y `SessionLocal` como `search_faq`:<br>• `ver_conocimiento()`: lista las áreas y categorías con su cantidad de FAQs.<br>• `buscar_conocimiento(consulta)`: devuelve el id, el área, la categoría, la pregunta y la respuesta.<br>• `proponer_cambio(accion, area, categoria, pregunta, respuesta, faq_id)`, con `accion` en `crear`, `editar`, `borrar` o `crear_categoria`. Guarda la propuesta con el `turn_id` del contexto y responde la guía fija `GUIA_PROPUESTA`: «Marco verá una tarjeta para confirmar. No digas que el cambio está hecho.» Si no valida, devuelve el texto de `create_proposal`.<br>Editar cambia solo la pregunta y la respuesta: la FAQ queda en su categoría. | RF-05 a RF-10 |

### New: `src/services/knowledge_card.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/knowledge_card.py` | `proposal_card(proposal, function_url, note="") -> dict`: arma un elemento de `cardsV2` con `cardId` = id de la propuesta.<br>• El encabezado dice qué cambio es; el cuerpo, el área › categoría, la pregunta y la respuesta resultantes.<br>• En una edición muestra también la versión actual; en un borrado, la FAQ que se borra.<br>• Lleva los botones «Confirmar»/«Cancelar», o «Guardar»/«Descartar» si la propuesta viene de una consulta. Cada uno tiene `onClick.action.function = function_url` y los parámetros `proposal` y `decision` (D6).<br>• Sin botones cuando la propuesta ya no está `pending`, con una nota de su estado (D9).<br>• El texto se escapa (`&`, `<`, `>`) porque los widgets interpretan HTML (D13).<br>`handle_click(session, event) -> dict` atiende el clic:<br>1. Comprueba el space de aviso (RF-19).<br>2. Si la decisión es aplicar, genera antes el embedding con `LLM.create(...).embeddings` y `to_embedding_text` (D7).<br>3. Llama a `resolve_proposal` y devuelve `update_message([card])`. | RF-07 a RF-09, RF-12 a RF-19, RF-21, RF-27 |

### New: `src/services/faq_learning.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/faq_learning.py` | `schedule_proposal(notice, thread, reply)` lanza una tarea propia, con el mismo patrón que `google_chat_reply`: `pending` y `cancel_pending()` (D11).<br>`propose_faq(...)`:<br>1. Busca FAQs parecidas a cada pregunta con `search_knowledge`.<br>2. Redacta con `llm.with_structured_output(FaqDraft)` (D12). El prompt fijo `PROPONER` recibe las áreas y categorías de `list_knowledge`, las parecidas, las preguntas y la respuesta de Marco, sin nombre ni número del contacto.<br>3. Descarta el borrador si viene con `reusable = False`, con `already_answered = True`, con un área que no está en la lista, o si contiene el nombre o los dígitos del número del contacto (D14).<br>4. Guarda la propuesta `origin = "consulta"` y la publica en el hilo con `create_message(..., cards, thread)`.<br>Todo dentro de `asyncio.timeout(PROPUESTA_LIMITE = 30)`, con una sesión propia. Un fallo solo deja el tipo de error en el log. | RF-20 a RF-27 |

### Changed
| File | Change | RF |
|---|---|---|
| `src/agents/graph.py` | `build_sub_agent(tools)` arma el grafo de un sub-agente. `sub_agent_graph` sigue con `[search_faq]`, y el nuevo `knowledge_graph` lleva las tres tools de conocimiento (D4). | RF-01, RF-02 |
| `src/agents/tools/delegate.py` | `delegar_area` usa `knowledge_graph` cuando el área es `CONOCIMIENTO = "Conocimiento"` (D3). Pasa al `AgentContext` el `turn_id`. | RF-01, RF-02 |
| `src/agents/context.py` | `AgentContext` y `CoordinatorContext` suman `turn_id: str = ""`. | RF-07 |
| `src/services/assistant.py` | `run_turn(..., turn_id=None, exclude=())`:<br>• usa `turn_id` como id del `HumanMessage` del turno y lo pasa al contexto;<br>• quita de los sub-agentes las áreas de `exclude`;<br>• solo lanza `AgentNotFoundError` si la audiencia no tiene sub-agentes antes de excluir, así que fuera del space de aviso el «Agente Interno» responde aunque «Conocimiento» sea su única área (D5). | RF-03, RF-04 |
| `src/services/google_chat_reply.py` | `process` devuelve `ChatAnswer(text, cards)` en vez de `str`. Con `whatsapp_notify_space` ausente, o con un space distinto, excluye `CONOCIMIENTO`. Tras el turno lee `proposals_of_turn` y arma sus tarjetas. `answer` responde `chat_reply(text, cards)` y `deliver` las pasa a `create_message` (RF-11). | RF-03, RF-04, RF-07, RF-11 |
| `src/interfaces/google_chat.py` | `AddonChat.button_clicked_payload` (con `message` y `space`) y `AddonEvent.common_event_object.parameters: dict[str, str]`. `chat_reply(text, cards=())` suma `cardsV2`. Nueva `update_message(cards)` con `hostAppDataAction.chatDataAction.updateMessageAction`. | RF-07, RF-11, RF-12 |
| `src/services/google_chat.py` | `create_message(space, text, token, http, cards=(), thread=None)`. Con hilo envía `thread.name` y `messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`. | RF-11, RF-21 |
| `src/routers/google_chat.py` | Antes del flujo de mensajes, un evento con `button_clicked_payload` va a `handle_click` (D10 del plan 008: un solo endpoint). | RF-12 a RF-19 |
| `src/services/whatsapp_relay.py` | Después de `add_marco_reply`, si el aviso tiene preguntas, llama a `schedule_proposal(notice, thread, reply)` con la respuesta original de Marco. El texto que devuelve no cambia. | RF-20, RF-25, RF-26 |
| `src/services/whatsapp.py` | `save_notice(session, thread, phone, questions)` guarda las preguntas del aviso. `NoticeTarget` suma `questions: tuple[str, ...]` (D10). | RF-20, RF-25 |
| `src/services/whatsapp_reply.py` | `notify` pasa a `save_notice` las preguntas del turno. | RF-20, RF-25 |
| `src/services/faq.py` + `src/rag/reindex.py` | `to_embedding_text` pasa a `services/faq.py` y `reindex` la importa de ahí (D15). | RF-14 |
| `main.py` | El `lifespan` cancela también las propuestas en curso con `faq_learning.cancel_pending()`. | RF-26 |

### Infrastructure
| File | Change |
|---|---|
| `src/models/knowledge.py`, `src/models/whatsapp.py` + `alembic/versions/<rev>_knowledge_proposals.py` | Tabla `knowledge_proposals` y columna `whatsapp_notices.questions`; `downgrade` las elimina. **Requiere aprobación de esquema.** |
| `tests/knowledge_test.py` | Tools, exclusión y wiring de grafos con `create_proposal`, `list_knowledge` y `search_knowledge` reemplazados (U-K*). |
| `tests/knowledge_card_test.py` | Tarjetas y clics con `resolve_proposal`, el LLM y las properties reemplazados (U-T*, U-B*). |
| `tests/faq_learning_test.py` | Propuestas desde la consulta con un LLM falso de salida estructurada y `create_message` reemplazado (U-L*). |
| `tests/google_chat_test.py`, `tests/google_chat_reply_test.py`, `tests/whatsapp_relay_test.py`, `tests/whatsapp_reply_test.py` | El endpoint con clics (E9 a E11), las respuestas con tarjetas y `create_message` con hilo. El relay llama a `schedule_proposal`, y `FakeStore.save_notice` recibe las preguntas. |
| `README.md` | El «Agente Interno», el área «Conocimiento» y su sub-agente con los `INSERT` y sus prompts, quién puede usarlo, las tarjetas, las propuestas desde el hilo y la tabla nueva. |
| `pyproject.toml` / `uv.lock` | 3.7.0 → 3.8.0 y `uv sync`, en `chore(release): Bump version to v3.8.0`. |

## Data model

```python
class KnowledgeProposal(Base):
  __tablename__ = "knowledge_proposals"
  __table_args__ = (
    CheckConstraint("action IN ('create', 'update', 'delete', 'category')", name = "ck_knowledge_proposals_action"),
    CheckConstraint("status IN ('pending', 'applied', 'discarded', 'rejected')", name = "ck_knowledge_proposals_status"),
    CheckConstraint("origin IN ('interno', 'consulta')", name = "ck_knowledge_proposals_origin"),
  )

  # uuid4: viaja en los parámetros del botón y no se puede adivinar (D1, D6).
  id: Mapped[str] = mapped_column(String(36), primary_key = True)
  # Turno del «Agente Interno» que la propuso, para armar las tarjetas de su respuesta (D2); nulo si viene de una consulta.
  turn_id: Mapped[str | None] = mapped_column(String(64), index = True)
  # interno: pedida al «Agente Interno» (Confirmar/Cancelar); consulta: propuesta desde el hilo (Guardar/Descartar).
  origin: Mapped[str] = mapped_column(String(20))
  action: Mapped[str] = mapped_column(String(20))
  area_id: Mapped[int] = mapped_column(ForeignKey("areas.id", ondelete = "CASCADE"))
  # Nombre de la categoría: si no existe en el área se crea al aplicar.
  category: Mapped[str] = mapped_column(String(255))
  # FAQ que se edita o borra; queda nula si se borra antes de confirmar (RF-17).
  faq_id: Mapped[int | None] = mapped_column(ForeignKey("faqs.id", ondelete = "SET NULL"))
  question: Mapped[str | None] = mapped_column(Text)
  answer: Mapped[str | None] = mapped_column(Text)
  # Versión de la FAQ al proponer: se muestra en la tarjeta y detecta cambios antes de aplicar (RF-08, RF-17, D8).
  previous_question: Mapped[str | None] = mapped_column(Text)
  previous_answer: Mapped[str | None] = mapped_column(Text)
  # pending → applied | discarded | rejected (duplicada o cambió); solo pending acepta botones (RF-18).
  status: Mapped[str] = mapped_column(String(20), server_default = "pending")
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())
  resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))

class WhatsappNotice(Base):
  # … columnas de la spec 008 sin cambios …
  # Preguntas de «Te consulta», una por línea; nula si el aviso no tenía consultas (RF-25, D10).
  questions: Mapped[str | None] = mapped_column(Text)
```

- Cada fila es una propuesta y muestra una tarjeta. Un turno puede crear varias, y una FAQ puede tener varias
  propuestas abiertas: la primera que se aplica deja a las demás como `rejected` por el chequeo de D8.
- La migración solo agrega cosas: una tabla nueva y una columna nula. La 3.7.0 no las lee, así que se puede aplicar
  antes del despliegue. `downgrade` elimina la columna y la tabla.
- Las propuestas resueltas se conservan como registro de los cambios. El volumen es de pocas filas al día, así que no
  hay limpieza.

## Decisions
| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Las propuestas se guardan en la tabla `knowledge_proposals` y el botón lleva solo su id | Llevar el cambio completo en los parámetros del botón, sin tabla | Con solo parámetros no se sabe si la tarjeta ya se confirmó o descartó, que es lo que pide RF-18. Un doble clic aplicaría dos veces, y el reintento tras un fallo de embedding (RF-15) no tendría estado. |
| D2 | La tool guarda la propuesta con el `turn_id` del contexto, y Google Chat arma las tarjetas con `proposals_of_turn` al terminar el turno | Leer las propuestas de los `tool_calls` del turno, como `questions_of` | Las tools del sub-agente corren dentro de `delegar_area`, y el turno del coordinador solo ve su texto final. Con el `turn_id`, el resultado no depende de lo que el modelo escriba en su respuesta (RF-07). |
| D3 | El sub-agente de conocimiento es el área `interno` llamada «Conocimiento» (constante `CONOCIMIENTO`) | Una columna nueva en `areas` con el tipo de sub-agente | Hay un solo sub-agente especial. El nombre ya es único (`areas.name UNIQUE`), y una columna sería otro cambio de esquema para un solo valor. |
| D4 | Un grafo propio `knowledge_graph` con `build_sub_agent(tools)`, y `sub_agent_graph` con solo `search_faq` | Sumar las tools de escritura al grafo compartido y filtrarlas por prompt | RF-02: los sub-agentes del chat web y de WhatsApp no deben poder escribir. Separar los grafos lo garantiza en el código, y U-K1 lo comprueba. |
| D5 | Fuera del space de aviso, `run_turn` excluye «Conocimiento»; el clic también comprueba el space | Comprobar el space solo dentro de las tools | Si el «Agente Interno» no ve el área, no ofrece lo que no puede hacer (RF-03, RF-04). El chequeo del clic cubre una tarjeta reenviada o un evento armado a mano (RF-19). |
| D6 | `onClick.action.function` es la property `google_chat_audience` | Una property nueva con la URL del endpoint | Un complemento HTTP exige la URL completa del endpoint en `action.function`. La audiencia del ID token ya es esa URL (README) y la usa `verify_token` (§6, sin duplicar configuración). |
| D7 | El embedding se genera al presionar el botón, antes de la transacción. Si falla, la propuesta sigue `pending` | Guardar la FAQ con `status = 're-index'` y esperar a `src.rag.reindex` | RF-14 pide que la FAQ se encuentre desde la siguiente consulta. Con `re-index`, el sub-agente no la vería hasta una reindexación manual. Mantenerla `pending` permite reintentar (RF-15). |
| D8 | Editar o borrar aplica con `WHERE id = :faq_id AND question = :previous_question AND answer = :previous_answer`; 0 filas deja la propuesta `rejected` | Una columna `version` en `faqs` | `faqs` no tiene versión, y cambiarla tocaría la reindexación y el modelo existente. Comparar contra la copia detecta cualquier cambio hecho desde la tarjeta o por SQL (RF-17). |
| D9 | `resolve_proposal` toma la fila con `SELECT … FOR UPDATE`, solo actúa si está `pending` y resuelve en la misma transacción | Confiar en que la tarjeta actualizada ya no muestra botones | Dos clics rápidos llegan antes de que la tarjeta se actualice. Con el bloqueo, el segundo ve el estado final y lo muestra (RF-18), como `add_marco_reply` en la spec 008. |
| D10 | Las preguntas del aviso se guardan en `whatsapp_notices.questions` | Reconstruirlas de los `tool_calls` de `consultar_a_marco` del historial del contacto | El historial junta las consultas de todos los avisos del contacto y no dice a cuál responde Marco. Guardarlas en el aviso responde a qué preguntó ese hilo y si tenía consultas (RF-20, RF-25). |
| D11 | La propuesta se redacta en una tarea aparte y se publica con la API en el hilo | Devolverla en la misma respuesta síncrona del relay | El relay ya usa hasta 25 de los ~30 s que espera Google Chat (D7 del plan 008). Separarla también cumple RF-26: si la propuesta falla, el envío al contacto y «Enviado a…» no cambian. |
| D12 | El LLM recibe las FAQs parecidas (`search_knowledge`) y decide `already_answered` y `reusable` en una salida estructurada (`FaqDraft`) | Un umbral fijo de distancia para detectar duplicadas | Que una FAQ «responda la pregunta» (RF-24) no se mide bien con una distancia: preguntas parecidas pueden tener respuestas distintas. La salida estructurada evita interpretar texto libre, y el LLM ya juzga si es reutilizable (RF-23). |
| D13 | El texto de las tarjetas se escapa antes de armarlas | Mandarlo tal cual | `textParagraph` y `decoratedText` interpretan HTML. Una respuesta con `<` o `&` se vería rota o con formato que nadie escribió. |
| D14 | Una guarda en código descarta el borrador que contiene el nombre del contacto o los dígitos de su número | Confiar solo en la instrucción del prompt | RF-22 es de privacidad y la FAQ queda visible en el chat web. El LLM nunca recibe esos datos, pero los puede repetir si vienen en la respuesta de Marco; la guarda es determinista y se puede probar. |
| D15 | `to_embedding_text` pasa a `src/services/faq.py` | Importarla desde `src.rag.reindex` en `services` | El script de reindexación depende de `services`, no al revés, así que importarla desde ahí dejaría una dependencia circular. Un solo formato de texto hace que las FAQs creadas desde Google Chat queden comparables con las de la reindexación (RF-14). |

## RF coverage
| RF | Where | Tests |
|---|---|---|
| RF-01, RF-02 | `knowledge_graph`, `delegar_area`, `TOOLS`/`WHATSAPP_TOOLS` sin cambios | U-K1, U-K2 |
| RF-03, RF-04 | `run_turn(exclude)`, `process` | U-K3 |
| RF-05 | `ver_conocimiento`, `list_knowledge` | U-K4 |
| RF-06 | `buscar_conocimiento`, `search_knowledge` | U-K5 |
| RF-07 | `proponer_cambio`, `proposals_of_turn`, `proposal_card` | U-K6, U-T1, U-T4 |
| RF-08, RF-09 | `create_proposal` (`previous_*`), `proposal_card` | U-K7, U-T2 |
| RF-10 | `create_proposal` | U-K8 |
| RF-11 | `process`, `answer`, `deliver`, `create_message(cards)` | U-T4, E11 |
| RF-12 | `handle_click`, `resolve_proposal` | U-B1, E9 |
| RF-13 | `handle_click` | U-B2 |
| RF-14 | `handle_click` (embedding), `to_embedding_text` | U-B1 |
| RF-15 | `handle_click` | U-B3 |
| RF-16 | `resolve_proposal` | U-B4 |
| RF-17 | `resolve_proposal` (D8) | U-B5 |
| RF-18 | `resolve_proposal` (D9), `proposal_card` | U-B6 |
| RF-19 | `handle_click` | U-B7 |
| RF-20 | `whatsapp_relay`, `schedule_proposal`, `propose_faq` | U-L1, U-L8 |
| RF-21 | `propose_faq`, `create_message(thread)` | U-L1, U-T1 |
| RF-22 | `propose_faq` (D14) | U-L2 |
| RF-23 | `propose_faq` | U-L3 |
| RF-24 | `propose_faq` (D12) | U-L4 |
| RF-25 | `whatsapp_relay`, `save_notice(questions)` | U-L5, U-L8 |
| RF-26 | `schedule_proposal` | U-L6 |
| RF-27 | `handle_click`, `propose_faq` | U-B8, U-L6 |

## Test strategy

**Unit — sub-agente de conocimiento (`tests/knowledge_test.py`)**
- U-K1 Las tools de conocimiento están solo en `knowledge_graph`: `sub_agent_graph` tiene solo `search_faq`, y `TOOLS` y `WHATSAPP_TOOLS` no las incluyen.
- U-K2 `delegar_area("Conocimiento", …)` invoca `knowledge_graph` y otra área invoca `sub_agent_graph`, los dos con el `turn_id` del contexto.
- U-K3 `run_turn(exclude={"Conocimiento"})` no pasa esa área al contexto. `process` la excluye en otro space y sin `whatsapp_notify_space`, y la deja en el space de aviso. Si «Conocimiento» es la única área `interno`, fuera del space de aviso igual responde sin `AgentNotFoundError`.
- U-K4 `ver_conocimiento` muestra cada área con sus categorías y la cantidad de FAQs.
- U-K5 `buscar_conocimiento` hace el embedding de la consulta y devuelve el id, el área, la categoría, la pregunta y la respuesta. Sin resultados, devuelve un texto que lo dice.
- U-K6 `proponer_cambio("crear", …)` guarda una propuesta `pending` con el `turn_id` y `origin = "interno"`, no escribe la FAQ y devuelve `GUIA_PROPUESTA`.
- U-K7 Editar o borrar copia la versión actual en `previous_*`. Un `faq_id` que no existe devuelve un error sin guardar la propuesta.
- U-K8 Un área inexistente, o una `interno`, devuelve las áreas `externo` disponibles sin guardar la propuesta.

**Unit — tarjetas (`tests/knowledge_card_test.py`)**
- U-T1 La tarjeta de crear muestra el área › categoría, la pregunta y la respuesta. Lleva «Confirmar» y «Cancelar» con `function = google_chat_audience` y los parámetros `proposal` y `decision`. Con `origin = "consulta"` los botones son «Guardar» y «Descartar».
- U-T2 La tarjeta de editar muestra la versión actual y la nueva; la de borrar, la FAQ que se borra.
- U-T3 `<b>&` en la respuesta llega escapado a la tarjeta.
- U-T4 `process` devuelve `ChatAnswer` con una tarjeta por cada propuesta del turno.
  - `answer` síncrona responde `chat_reply` con `text` y `cardsV2`.
  - La diferida llama a `create_message` con las mismas tarjetas.
  - Sin propuestas no hay `cardsV2`.

**Unit — clics (`tests/knowledge_card_test.py`)**, con eventos `buttonClickedPayload` en el space de aviso:
- U-B1 Con `decision = apply`, se genera el embedding con `to_embedding_text` y se llama a `resolve_proposal` con él. La respuesta es un `updateMessageAction` cuya tarjeta no tiene botones y dice que se aplicó.
- U-B2 Con `decision = discard`, no hay embedding, se descarta la propuesta y la tarjeta queda sin botones con «Descartada».
- U-B3 Si el embedding falla, no se llama a `resolve_proposal` y la tarjeta conserva los botones con la nota del error.
- U-B4 Si `resolve_proposal` devuelve `rejected` por duplicada, la tarjeta dice que ya existe y queda sin botones.
- U-B5 Si devuelve `rejected` porque la FAQ cambió o se borró, la tarjeta lo dice y queda sin botones.
- U-B6 Con una propuesta ya `applied` o `discarded`, no se genera el embedding y la tarjeta muestra ese estado.
- U-B7 Un clic en otro space, o sin `whatsapp_notify_space`, responde un texto sin tocar la propuesta.
- U-B8 Con fallos de embedding o de BD, `caplog` no contiene la pregunta ni la respuesta.

**Unit — propuestas desde la consulta (`tests/faq_learning_test.py`)**, con un LLM falso de salida estructurada:
- U-L1 Una respuesta enviada en el hilo de un aviso con preguntas programa la propuesta.
  - El borrador válido se guarda con `origin = "consulta"`.
  - `create_message` recibe el hilo del aviso y una tarjeta con «Guardar»/«Descartar».
- U-L2 El prompt no lleva el nombre ni el número del contacto. Un borrador que trae el nombre o los dígitos del número no se guarda ni se publica.
- U-L3 Con `reusable = False` no hay propuesta.
- U-L4 Con `already_answered = True` no hay propuesta. El prompt incluye las FAQs parecidas de `search_knowledge`.
- U-L5 Un aviso sin preguntas no programa la propuesta.
- U-L6 Si el LLM falla o supera el límite, o `create_message` falla, el relay devuelve igual «Enviado a…». El log no trae la pregunta ni la respuesta.
- U-L7 Un borrador con un área que no está en `list_knowledge` no se guarda.
- U-L8 `notify` guarda el aviso con sus preguntas, y `find_notice` las devuelve en `NoticeTarget.questions` (`FakeStore`).

**E2E (`tests/google_chat_test.py`)**
- E9 Un clic firmado responde con lo que devuelve `handle_click`, sin llamar a `relay_reply` ni a `answer`.
- E10 Un clic sin token, o con un token que no es válido, responde `401` sin llamar a `handle_click`.
- E11 `AddonEvent` parsea `chat.buttonClickedPayload.space` y `commonEventObject.parameters`. `create_message` con tarjetas y hilo envía `cardsV2`, `thread.name` y `messageReplyOption`.

**Limitaciones**
- Que el complemento reciba los clics de una tarjeta publicada con la API en el mismo endpoint y con el mismo token, y que `updateMessageAction` actualice esa tarjeta, no se puede probar con fakes. Se comprueba en el paso 1 y al final, a mano.
- El SQL nuevo se prueba con un script contra la BD local, como en las specs 007 y 008:
  - `list_knowledge` y `search_knowledge`;
  - `create_proposal`;
  - `resolve_proposal`: duplicada, cambiada, borrada, categoría nueva, doble resolución;
  - `save_notice` y `find_notice` con preguntas.
- La calidad de las propuestas, es decir si el LLM redacta bien y juzga bien qué es reutilizable, se juzga en la verificación manual.

## Implementation order
1. **Comprobación manual de los clics** (gate). Con un script se publica en `spaces/g4uwxqAAAAE` una tarjeta con un botón, usando la cuenta de servicio y `function = google_chat_audience`. Marco presiona el botón y el log del pod debe mostrar `POST /api/v1/google-chat/events` con `200`, no `401`. La 3.7.0 ignora el evento y responde `{}`. Si no llega o llega con `401`, se revisa la configuración del complemento antes de seguir.
2. Modelo, migración y `alembic check`, con la aprobación de esquema. `to_embedding_text` pasa a `services/faq.py`.
3. `services/knowledge.py` y el script de SQL contra la BD local.
4. Contextos con `turn_id`, `build_sub_agent`, `knowledge_graph`, las tools de conocimiento, `delegar_area` y `run_turn(exclude)` (U-K*).
5. Interfaces de tarjetas y clics, `create_message` con tarjetas e hilo, `knowledge_card.py` y `ChatAnswer` en `google_chat_reply` (U-T*).
6. `handle_click` y el router (U-B*, E9 a E11).
7. `save_notice` con preguntas, `faq_learning.py`, la llamada desde el relay y `cancel_pending` en `main.py` (U-L*).
8. README, con los `INSERT` del «Agente Interno», el área «Conocimiento» y su sub-agente.
9. Versión 3.8.0 y `uv sync`.

## Release
1. `entrypoint.sh` aplica la migración al desplegar. También se puede aplicar antes en la BD remota, porque la 3.7.0 no lee la tabla ni la columna.
2. Merge a `main`: `release.yaml` despliega en el namespace `virtual-assistant`.
3. Aplicar los `INSERT` del README, revisados por Marco: el «Agente Interno» con su prompt, el área `interno` «Conocimiento» y el «Agente Conocimiento» con su prompt.
4. Los avisos publicados antes de la 3.8.0 no tienen preguntas guardadas, así que sus hilos no generan propuestas. Queda dicho en el README.
5. Verificación manual de la spec.

## Risks
- **Los clics no llegan, o `updateMessageAction` no actualiza una tarjeta publicada con la API:** las confirmaciones no funcionan.
  - Se comprueba primero (paso 1).
  - Si la actualización falla, la alternativa es responder el resultado como mensaje nuevo con `createMessageAction` y dejar la tarjeta como está; el estado en la tabla sigue evitando el doble aplicado (D9).
- **El modelo dice «listo» sin que Marco confirme:** la guía de `proponer_cambio` y el prompt lo prohíben. Aunque lo diga, nada se escribe sin el botón, que es lo que importa.
- **Propuestas de baja calidad o demasiadas:** cada respuesta en un hilo de consulta puede proponer. Se calibra con `PROPONER` y Marco las descarta con un clic. Si molestan, se puede limitar a la primera respuesta del hilo sin cambiar el esquema.
- **Una FAQ con datos de otra persona:** la guarda de D14 solo cubre el nombre y el número del contacto, no otros datos personales que Marco escriba. La tarjeta muestra el texto exacto antes de guardar.
- **Fuera del space de aviso el «Agente Interno» queda sin áreas** si «Conocimiento» es la única `interno`: responde solo con su prompt (D5). El README recomienda que el prompt lo diga.
