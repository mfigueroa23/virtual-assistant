# Plan 011 — Audiencia personal para WhatsApp y administración de todo el conocimiento

- **Spec:** [spec.md](spec.md) (approved 2026-10-26)
- **Status:** Approved (2026-10-27: esquema, prompt base de D7 y D2 aprobados)
- **Version:** 3.9.0 (feat → minor, desde 3.8.1)

El plan respeta la constitución:
- §2: el router de Google Chat no cambia. Las áreas nuevas, sus sub-agentes y las propuestas viven en
  `services/knowledge.py`, el único lugar con el SQL del conocimiento.
- §4: los tests corren sin BD real, sin LLM y sin Google, con los fakes de `tests/knowledge_test.py`,
  `tests/knowledge_card_test.py`, `tests/faq_learning_test.py` y `tests/whatsapp_reply_test.py`. El SQL se prueba con
  un script contra la BD local, como en el plan 009.
- §6: no hay properties nuevas.
- §7: Marco ve tarjetas y textos en español. El log guarda solo el tipo de error, nunca preguntas, respuestas ni
  descripciones de áreas (RF-29).

No agrega dependencias. **Requiere aprobación de esquema:**
- el check `ck_areas_audiencia` acepta `personal`;
- `knowledge_proposals` admite la acción `area`, con tres columnas nuevas y `area_id` y `category` nulos.

Los detalles y la migración están en **Data model**. Los prompts del «Agente Interno» y del «Agente Conocimiento», y la
descripción del área «Conocimiento», son datos. Los `UPDATE` van en el README y Marco los revisa antes de aplicarlos en
producción.

## Endpoints

Sin endpoints nuevos.

| Método y ruta | Auth | Respuesta | RF |
|---|---|---|---|
| `POST /api/v1/google-chat/events` (existente) | ID token de Google, sin cambios | Igual que en el plan 009. Las tarjetas muestran la audiencia y puede haber tarjetas de creación de área. Un clic en una de ellas actualiza la tarjeta con `updateMessageAction` | RF-10 a RF-22, RF-25 |
| `POST /api/v1/whatsapp/events` (existente) | Firma de YCloud, sin cambios | Sin cambios. El turno del «Agente WhatsApp» ve las áreas `personal` y `externo` | RF-02, RF-04 a RF-07 |
| `POST /api/v1/chat` (existente) | Sin cambios | Sin cambios. Sigue viendo solo `externo` | RF-03 |

## Modules

### Changed: audiencias de los coordinadores
| File | Change | RF |
|---|---|---|
| `src/services/agents.py` | `SubAgentConfig` suma `audiencia`. `list_sub_agents(session, audiencias: Sequence[str])` filtra con `Area.audiencia.in_(audiencias)` y devuelve también la audiencia del área (D1). | RF-02 a RF-04, RF-17 |
| `src/services/assistant.py` | `run_turn` y `ask` reciben `audiencias: Sequence[str]` en vez de `audiencia: str`. `AgentNotFoundError` se mantiene si ninguna de las audiencias tiene sub-agentes. | RF-02, RF-07 |
| `src/agents/context.py` | `CoordinatorContext` pierde `audiencia`: cada sub-agente trae la suya en `SubAgentConfig` (D2). `AgentContext.audiencia` queda y pasa a ser la del área. | RF-05 |
| `src/agents/tools/delegate.py` | `AgentContext(audiencia = sub_agent.audiencia, …)`. `is_knowledge` pasa a ser `sub_agent.audiencia == "interno" and sub_agent.area == CONOCIMIENTO` (D2). | RF-05 |
| `src/services/whatsapp_reply.py` | `AUDIENCIA = "externo"` pasa a ser `AUDIENCIAS = ("personal", "externo")`, sin depender de `turn_instructions` ni del trato familiar (D3). | RF-02, RF-04, RF-06, RF-07 |
| `src/routers/chat.py`, `src/services/google_chat_reply.py` | Pasan `("externo",)` e `("interno",)`. Cambia la firma, no el comportamiento. | RF-03 |

### Changed: `src/services/knowledge.py`
| File | Change | RF |
|---|---|---|
| `src/services/knowledge.py` | **Audiencias (D4).** `AUDIENCIA = "externo"` pasa a ser `AUDIENCIAS = ("externo", "interno", "personal")`.<br>• `list_knowledge`, `search_knowledge` y `create_proposal` reciben `audiencias` (por defecto las tres) y filtran con `in_`.<br>• `AreaSummary`, `KnowledgeHit` y `Proposal` suman `audiencia`.<br>• `external_areas` pasa a ser `areas_text(session, audiencias)`: las áreas agrupadas por audiencia, para el error de RF-12.<br>**Lectura de propuestas.** `get_proposal`, `proposals_of_turn` y `resolve_proposal` cambian el `join` con `Area` por un `outerjoin`. Una propuesta `area` toma el nombre y la audiencia de sus columnas propias; si ya se aplicó, del área creada.<br>**Crear áreas.** Nueva `create_area_proposal(session, name, audiencia, description, turn_id) -> Proposal \| str`:<br>• valida nombre y descripción no vacíos y la audiencia (RF-21);<br>• valida que no exista un área con ese nombre en ninguna audiencia (RF-19);<br>• guarda la fila `pending` con `action = "area"`.<br>`apply` suma la rama `area` (D6):<br>• si ya existe el área o el agente `Agente <nombre>`, devuelve `duplicate` (RF-20);<br>• si no, inserta el área y su `Agents` con `base_prompt(audiencia, name, description)`, y guarda el `area_id` creado en la propuesta.<br>Nueva `base_prompt(audiencia, name, description)`, con una plantilla fija por audiencia en `PROMPTS_BASE` (D7). | RF-08, RF-09, RF-11, RF-12, RF-14 a RF-21, RF-28 |

### Changed: sub-agente de conocimiento y tarjetas
| File | Change | RF |
|---|---|---|
| `src/agents/tools/knowledge.py` | • `ver_conocimiento` agrupa por audiencia.<br>• `buscar_conocimiento` muestra `[id N] audiencia · área › categoría`.<br>• `proponer_cambio` no cambia de firma: el área decide la audiencia.<br>• Nueva tool `proponer_area(nombre, audiencia: Literal["externo", "interno", "personal"], descripcion)`. Llama a `create_area_proposal` con el `turn_id` y responde `GUIA_PROPUESTA`, o el error de validación (D5).<br>`KNOWLEDGE_TOOLS` suma `proponer_area`. | RF-08 a RF-12, RF-14, RF-19, RF-21 |
| `src/services/knowledge_card.py` | • El subtítulo de toda tarjeta pasa a ser `audiencia · área › categoría`, o `audiencia · área` en una de área (RF-11, RF-25).<br>• `proposal_card` suma la rama `area`, con nombre, audiencia y descripción, y `TITULOS["area"] = "Nueva área"`.<br>• `APLICADO["area"]` dice que se creó el área con su sub-agente; la nota `duplicate` de una propuesta de área dice que ya existe un área o un agente con ese nombre.<br>• `handle_click` no genera embedding para `area`, igual que para `category`; el resto del flujo no cambia (RF-22). | RF-11, RF-14, RF-15, RF-18, RF-20, RF-22, RF-25 |

### Changed: propuestas desde consultas
| File | Change | RF |
|---|---|---|
| `src/services/faq_learning.py` | `draft_proposal` (D8):<br>• Las áreas del pedido salen de `list_knowledge(session, ("personal",))`. Sin áreas `personal` no se llama al LLM ni se propone, y el log solo lo dice, sin contenido (RF-26).<br>• Las FAQs parecidas salen de `search_knowledge(session, v, ("personal", "externo"))` y llevan la audiencia en el pedido (RF-24).<br>• Un borrador con un área que no está en la lista `personal` se descarta (RF-27).<br>• Guarda con `create_proposal(..., audiencias = ("personal",))`, como segunda guarda.<br>`PROPONER` dice que el área es una de las `personal` de la lista. | RF-23, RF-24, RF-26, RF-27 |

### Infrastructure
| File | Change |
|---|---|
| `src/models/areas.py`, `src/models/knowledge.py` + `alembic/versions/<rev>_personal_audience.py` | Check de audiencias y cambios de `knowledge_proposals` (ver **Data model**). Alembic no detecta los cambios de un `CheckConstraint`, así que la migración se completa a mano y `downgrade` la revierte. **Requiere aprobación de esquema.** |
| `tests/whatsapp_reply_test.py`, `tests/knowledge_test.py`, `tests/google_chat_reply_test.py`, `tests/cors_test.py` | Los fakes de `run_turn`, `ask` y `list_sub_agents` reciben `audiencias`. `SubAgentConfig` y `CoordinatorContext` cambian de forma (U-P*, U-C*, U-N*). |
| `tests/knowledge_card_test.py` | Tarjetas con audiencia y de área, y clics en ellas (U-T*). |
| `tests/faq_learning_test.py` | Propuestas solo en `personal` (U-F*). |
| `README.md` | La audiencia `personal` y qué ve cada coordinador, la creación de áreas, dónde quedan las FAQs aprendidas, la tabla `areas` con las tres audiencias y los `UPDATE` de los prompts. |
| `pyproject.toml` / `uv.lock` | 3.8.1 → 3.9.0 y `uv sync`, en `chore(release): Bump version to v3.9.0`. |

## Data model

```python
class Area(Base):
  __tablename__ = "areas"
  __table_args__ = (
    # personal: áreas que solo ve el «Agente WhatsApp», junto con las externo (RF-01 de la spec 011).
    CheckConstraint("audiencia IN ('externo', 'interno', 'personal')", name="ck_areas_audiencia"),
  )
  # … columnas sin cambios …

class KnowledgeProposal(Base):
  __tablename__ = "knowledge_proposals"
  __table_args__ = (
    CheckConstraint("action IN ('create', 'update', 'delete', 'category', 'area')", name = "ck_knowledge_proposals_action"),
    # Una propuesta de área lleva sus datos; las demás, su área y su categoría (D6).
    CheckConstraint(
      "(action = 'area' AND area_name IS NOT NULL AND area_description IS NOT NULL AND area_audiencia IS NOT NULL)"
      " OR (action <> 'area' AND area_id IS NOT NULL AND category IS NOT NULL)",
      name = "ck_knowledge_proposals_area"
    ),
    # … status y origin sin cambios …
  )
  # Nula en una propuesta de área hasta aplicarla; después, el área creada.
  area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete = "CASCADE"))
  # Nula en una propuesta de área.
  category: Mapped[str | None] = mapped_column(String(255))
  # Área que se propone crear: nombre, descripción y audiencia (RF-14).
  area_name: Mapped[str | None] = mapped_column(String(255))
  area_description: Mapped[str | None] = mapped_column(Text)
  area_audiencia: Mapped[str | None] = mapped_column(String(20))
  # … resto de columnas sin cambios …
```

- **Áreas:** cada una sigue teniendo una sola audiencia y un solo sub-agente (`uq_agents_area_id`). El nombre del área
  sigue siendo único entre las tres audiencias (`areas.name UNIQUE`).
- **Propuestas de área:** el área no existe al proponer, por eso `area_id` es nulo. Al aplicar se guarda el id del
  área creada, que queda como registro.
- **Compatibilidad (RF-28):** la migración solo relaja nulos, agrega columnas nulas y amplía los checks. Las filas
  existentes cumplen el check nuevo porque tienen `area_id` y `category`. La 3.8.1 sigue funcionando con el esquema
  nuevo, así que la migración se puede aplicar antes del despliegue.
- **`downgrade`:** borra las propuestas `area`, vuelve los checks a los de la 3.8.1, quita las tres columnas y deja
  `area_id` y `category` como `NOT NULL`. Si hay áreas `personal`, falla, y hay que borrarlas o cambiarles la
  audiencia antes de bajar.

## Decisions
| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | `list_sub_agents`, `run_turn` y `ask` reciben una lista de audiencias | Que la audiencia `personal` incluya implícitamente las `externo` dentro de `list_sub_agents` | RF-02 pide dos audiencias para un solo coordinador. Si la lista es explícita, cada canal declara lo que ve y RF-03/RF-04 se prueban mirando qué pasa cada canal. Una regla escondida en el servicio afectaría a cualquier coordinador que use `personal`. |
| D2 | La audiencia viaja en cada `SubAgentConfig`. `delegar_area` la pasa al `AgentContext`, y `CoordinatorContext.audiencia` desaparece | Mantener `CoordinatorContext.audiencia` y pasarla al sub-agente, como hoy | Con dos audiencias, la del coordinador ya no dice dónde buscar. `search_faq` filtra por `context.audiencia`, y RF-05 pide que cada sub-agente busque en su área. Además, la elección de `knowledge_graph` (D4 del plan 009) pasa a depender del área y no del canal. |
| D3 | WhatsApp pasa `("personal", "externo")` en todos los turnos, sin mirar `whatsapp_family_phones` | Filtrar áreas según el contacto | RF-06 y la decisión de la spec: todos los contactos ven lo mismo. Si no se mira el contacto, una lista de familia mal escrita no puede dejar a alguien sin las áreas. |
| D4 | `services/knowledge.py` recibe las audiencias como parámetro, con las tres por defecto | Funciones separadas por audiencia, o mantener la constante y filtrar después en Python | El mismo SQL sirve a «Conocimiento» (tres audiencias, RF-08 a RF-13) y a las propuestas desde consultas (`personal` y `externo`, RF-23, RF-24). Filtrar en Python rompería el `limit(top_k)` de `search_knowledge`. |
| D5 | Una tool nueva, `proponer_area`, con la audiencia como `Literal` | Sumar `crear_area` a las acciones de `proponer_cambio` | Los argumentos de un área (nombre, audiencia y descripción) no se parecen a los de una FAQ. Juntarlos haría que el modelo llene campos que no corresponden. Con el `Literal`, el esquema de la tool ya limita la audiencia, y el servicio la vuelve a validar (RF-21). |
| D6 | La creación de un área es una fila más de `knowledge_proposals` (`action = 'area'`), con columnas propias y `area_id` nulo hasta aplicarla | Una tabla `area_proposals` aparte | Las tarjetas (`turn_cards`), los clics (`handle_click`), el bloqueo `FOR UPDATE` y los estados (RF-18 de la spec 009) ya trabajan sobre esa tabla y un solo id. Otra tabla duplicaría ese flujo y haría que `proposals_of_turn` juntara dos fuentes. |
| D7 | El prompt del sub-agente nuevo sale de una plantilla fija por audiencia, en el código, con el nombre y la descripción del área | Que el LLM o Marco redacten el prompt al pedir el área | La spec fija un prompt base y deja fuera editar prompts. Un prompt redactado por el modelo podría saltarse las reglas de `search_faq` o de privacidad; con la plantilla, el sub-agente de un área `personal` o `externo` nace con las mismas reglas que los demás. |
| D8 | Las propuestas desde consultas solo pasan las áreas `personal` al LLM y lo vuelven a comprobar en código y en `create_proposal` | Confiar en que el prompt `PROPONER` elija bien el área | RF-23 y RF-27 son de privacidad: algo dicho por WhatsApp no debe llegar al chat web. Si el LLM nunca ve un área `externo`, no puede elegirla, y las dos guardas en código se pueden probar. |
| D9 | Para saber si ya hay respuesta, se buscan FAQs en `personal` y `externo` | Buscar solo en `personal` | RF-24: si el portafolio ya responde la pregunta, WhatsApp también la ve (RF-02), así que una FAQ personal igual sería un duplicado. |
| D10 | Una propuesta de área aplicada devuelve `duplicate` si ya existe el área **o** el agente `Agente <nombre>` | Comprobar solo el nombre del área | `agents.name` es único y ya hay coordinadores como «Agente Externo». Un área llamada «Externo» chocaría con el `INSERT` del agente y la transacción fallaría con un error genérico en vez de la nota de RF-20. |
| D11 | Los sub-agentes nuevos quedan disponibles por la lectura que ya se hace en cada turno, sin caché | Recargar o invalidar algo al crear el área | `run_turn` llama a `list_sub_agents` en cada turno (plan 009, sin caché), así que RF-17 se cumple sin código extra. U-N6 lo comprueba. |

## RF coverage
| RF | Where | Tests |
|---|---|---|
| RF-01 | `ck_areas_audiencia`, migración | Script SQL, U-N2 |
| RF-02, RF-04 | `whatsapp_reply.AUDIENCIAS`, `list_sub_agents(audiencias)` | U-P1, U-P2 |
| RF-03 | `routers/chat.py`, `google_chat_reply.process` | U-P3 |
| RF-05 | `SubAgentConfig.audiencia`, `delegar_area`, `search_faq` | U-P4, U-P5 |
| RF-06 | `whatsapp_reply` (D3) | U-P6 |
| RF-07 | `list_sub_agents`, `run_turn` | U-P7 |
| RF-08 | `ver_conocimiento`, `list_knowledge` | U-C1 |
| RF-09 | `buscar_conocimiento`, `search_knowledge` | U-C2 |
| RF-10, RF-11 | `proponer_cambio`, `create_proposal`, `proposal_card` | U-C3, U-T1 |
| RF-12 | `create_proposal`, `areas_text` | U-C4 |
| RF-13 | `handle_click`, `resolve_proposal` | U-T2 |
| RF-14 | `proponer_area`, `create_area_proposal`, `proposal_card` | U-N1, U-T3 |
| RF-15, RF-16 | `apply` (rama `area`), `base_prompt`, `handle_click` | U-N2, U-N3, U-T4 |
| RF-17 | `run_turn` sin caché (D11) | U-N6 |
| RF-18 | `handle_click` | U-T5 |
| RF-19 | `create_area_proposal` | U-N4 |
| RF-20 | `apply` (D10) | U-N5, U-T6 |
| RF-21 | `proponer_area`, `create_area_proposal` | U-N7 |
| RF-22 | `handle_click` | U-T7 |
| RF-23 | `draft_proposal` (D8) | U-F1 |
| RF-24 | `draft_proposal` (D9) | U-F2 |
| RF-25 | `proposal_card` | U-T8 |
| RF-26 | `draft_proposal` | U-F3 |
| RF-27 | `draft_proposal` | U-F4 |
| RF-28 | Migración, `outerjoin` en las lecturas | U-T9, script SQL |
| RF-29 | `handle_click`, `propose_faq`, `create_area_proposal` | U-T10, U-F5 |

## Test strategy

**Unit — audiencias (`tests/whatsapp_reply_test.py`, `tests/knowledge_test.py`)**
- U-P1 Un lote de WhatsApp llama a `run_turn` con `("personal", "externo")` y el coordinador «Agente WhatsApp» (reemplaza la aserción de U-W5).
- U-P2 `run_turn` pone en `CoordinatorContext.sub_agents` las áreas de `list_sub_agents`. El fake registra las audiencias pedidas, y WhatsApp nunca pide `interno`.
- U-P3 `/chat` pide `("externo",)` y Google Chat pide `("interno",)`.
- U-P4 `delegar_area` pasa al `AgentContext` la audiencia del área (`personal` o `externo`), no la del coordinador.
- U-P5 `search_faq` llama a `search_faqs` con el área y la audiencia del contexto del sub-agente.
- U-P6 Un contacto que está en `whatsapp_family_phones` y uno que no pasan las mismas audiencias.
- U-P7 Si `list_sub_agents` devuelve solo áreas `externo`, el turno responde. Si no devuelve ninguna, lanza `AgentNotFoundError` como antes.

**Unit — conocimiento en tres audiencias (`tests/knowledge_test.py`)**
- U-C1 `ver_conocimiento` agrupa por audiencia: `personal`, `externo` e `interno` con sus categorías y cantidades.
- U-C2 `buscar_conocimiento` muestra el id, la audiencia, el área, la categoría, la pregunta y la respuesta.
- U-C3 `proponer_cambio` sobre un área `interno` o `personal` guarda la propuesta sin restringir la audiencia.
- U-C4 Un área inexistente devuelve el texto de `create_proposal` con las áreas agrupadas por audiencia, sin guardar.

**Unit — crear áreas (`tests/knowledge_test.py`)**
- U-N1 `proponer_area("Amigos", "personal", "…")` guarda una propuesta `area` `pending` con el `turn_id`, no crea nada y devuelve `GUIA_PROPUESTA`.
- U-N2 `base_prompt` de cada audiencia incluye el nombre y la descripción del área, la instrucción de usar `search_faq` y la de no inventar.
- U-N3 La rama `area` de `apply` crea el área y «Agente \<nombre\>» con `base_prompt`, y guarda el `area_id` en la propuesta (sesión falsa que registra los `add`).
- U-N4 Un nombre que ya existe devuelve un texto con la audiencia del área existente y no guarda la propuesta.
- U-N5 Si al aplicar ya existe el área o el agente, el resultado es `duplicate` y no se agrega nada.
- U-N6 Después de aplicar, un `run_turn` de la audiencia del área recibe el sub-agente nuevo, sin reiniciar (fake de `list_sub_agents` que lee la lista actual).
- U-N7 Sin nombre, sin descripción o con una audiencia inválida, `create_area_proposal` devuelve qué falta o las audiencias válidas, sin guardar.

**Unit — tarjetas y clics (`tests/knowledge_card_test.py`)**
- U-T1 El subtítulo de la tarjeta de una FAQ es `audiencia · área › categoría`.
- U-T2 Un clic en una tarjeta de FAQ de un área `interno` o `personal` aplica con embedding, como en la spec 009.
- U-T3 La tarjeta de área muestra el nombre, la audiencia y la descripción, con «Confirmar» y «Cancelar».
- U-T4 «Confirmar» en una tarjeta de área no genera embedding. Llama a `resolve_proposal` y deja la tarjeta sin botones, con la nota de área creada.
- U-T5 «Cancelar» la deja «Descartada», sin botones.
- U-T6 Con `duplicate`, la nota dice que ya existe un área o un agente con ese nombre.
- U-T7 Un segundo clic, o un clic fuera del space de aviso, no aplica nada (como U-B6 y U-B7 del plan 009).
- U-T8 La tarjeta de una propuesta desde consulta muestra la audiencia `personal`.
- U-T9 Una propuesta sin `audiencia` en el área (fila anterior a esta spec) se arma y se resuelve como antes.
- U-T10 Si falla la creación de un área, `caplog` no contiene el nombre ni la descripción.

**Unit — propuestas desde consultas (`tests/faq_learning_test.py`)**
- U-F1 El pedido al LLM lista solo las áreas `personal` y el borrador se guarda con `audiencias = ("personal",)`.
- U-F2 `search_knowledge` se llama con `("personal", "externo")`. Una FAQ `externo` parecida aparece en el pedido con su audiencia.
- U-F3 Sin áreas `personal` no se llama al LLM ni a `create_message`, y el relay devuelve igual «Enviado a…».
- U-F4 Un borrador con un área `externo` o inexistente no se guarda ni se publica.
- U-F5 En los fallos de U-F3 y U-F4, el log no trae la pregunta ni la respuesta.

**Limitaciones**
- Los fakes no prueban los checks ni el `outerjoin`. Se prueban con un script contra la BD local:
  - `upgrade` con propuestas de la 3.8.1 ya guardadas;
  - un `INSERT` de área `personal`;
  - una propuesta `area` aplicada y su `area_id`;
  - el check `ck_knowledge_proposals_area`, que rechaza filas incompletas;
  - `downgrade` sin áreas `personal`.
- La migración de los checks se escribe a mano. `alembic check` no ve diferencias en un `CheckConstraint`, así que pasar no prueba que esté bien: lo confirma el script.
- Si el «Agente WhatsApp» elige bien entre áreas `personal` y `externo` depende del modelo y de las descripciones de las áreas. Se juzga en la verificación manual.

## Implementation order
1. Modelos y migración, con la aprobación de esquema. Script SQL contra la BD local con `upgrade`, los checks y `downgrade`.
2. `SubAgentConfig.audiencia`, `list_sub_agents(audiencias)`, `run_turn` y `ask` con `audiencias`, `CoordinatorContext` sin `audiencia`, y `delegar_area`. Ajustar los llamadores (`chat`, `google_chat_reply`, `whatsapp_reply`) y los fakes (U-P*).
3. `services/knowledge.py`: audiencias, `audiencia` en los dataclasses, `outerjoin`, `areas_text`, `create_area_proposal`, la rama `area` de `apply` y `base_prompt`. Volver a correr el script SQL.
4. Tools de conocimiento y `proponer_area` (U-C*, U-N*).
5. `knowledge_card.py`: subtítulo con audiencia, tarjeta de área y clic sin embedding (U-T*).
6. `faq_learning.py` solo con `personal` (U-F*).
7. README: audiencias, creación de áreas, FAQs aprendidas y `UPDATE` de prompts.
8. Versión 3.9.0 y `uv sync`.

## Release
1. `entrypoint.sh` aplica la migración al desplegar. Como la 3.8.1 funciona con el esquema nuevo, también se puede aplicar antes en la BD remota.
2. Merge a `main`: `release.yaml` despliega en el namespace `virtual-assistant`.
3. Aplicar los `UPDATE` del README, revisados por Marco:
   - prompt del «Agente Conocimiento»: tres audiencias y `proponer_area`;
   - prompt del «Agente Interno»: ya no dice «preguntas frecuentes del chat web y del WhatsApp»;
   - descripción del área «Conocimiento».
4. Crear desde el space de aviso las áreas `personal` iniciales. Hasta que exista alguna, WhatsApp responde como en la 3.8.1 y las consultas no generan propuestas (RF-07, RF-26).
5. Verificación manual de la spec.

## Risks
- **Lo que se guarda en `personal` lo ve cualquiera que escriba por WhatsApp** (RF-06):
  - un desconocido puede preguntar por algo que Marco guardó pensando en amigos;
  - el README y el prompt base de `personal` lo advierten;
  - cada tarjeta muestra la audiencia antes de confirmar.
- **El modelo elige la audiencia equivocada al proponer un área:** por ejemplo, una `externo` con contenido personal. La tarjeta muestra la audiencia y nada se crea sin «Confirmar». Cambiar la audiencia de un área existente queda fuera de alcance, así que hay que hacerlo por SQL.
- **Más áreas en WhatsApp, más delegaciones:** un turno que consulta un área `personal` y otra `externo` usa más tiempo de `whatsapp_timeout`. Se mide en la verificación manual y, si hace falta, se sube la property.
- **El check de `downgrade` falla con áreas `personal`:** volver a la 3.8.1 exige borrarlas o moverlas antes. Queda dicho en el README.
- **La descripción de `search_faq` todavía menciona Autofin** (fuera de alcance de la spec 009). Con áreas `personal`, el modelo puede creer que la tool no aplica. Si la prueba manual lo muestra, se registra aparte.
