# Plan 007 — Derivación, encargos y círculo familiar en WhatsApp

- **Spec:** [spec.md](spec.md) (approved 2026-10-20)
- **Status:** Approved
- **Version:** 3.6.0 (feat → minor, desde 3.5.0)

El plan respeta la constitución:
- §2: la lógica queda en `services` y los routers solo cambian el grafo que pasan.
- §4: los tests corren sin BD real, sin LLM, sin YCloud y sin Google; la red se simula con el backend de pruebas de `httpcore`.
- §6: el space de aviso es una property.
- §7: el contacto nunca ve errores y el log no guarda el contenido de la conversación.

No agrega dependencias. **Requiere aprobación de esquema:** la columna `contact_name` en `whatsapp_conversations`, con su migración (ver **Data model**). El usuario aprobó la spec junto con el posible cambio de esquema y con el cambio de RF-16 de la spec 006. Los criterios de derivación y de encargos, el trato profesional, el space de aviso, los encargos aceptables, los números familiares y las instrucciones familiares son datos: se cargan con el SQL del README.

## Endpoints

| Método y ruta | Auth | Respuesta | RF |
|---|---|---|---|
| `POST /api/v1/whatsapp/events` (existente) | `YCloud-Signature`, sin cambios | Sin cambios. Ahora entrega los eventos al grafo de WhatsApp, que incluye la acción de derivar | RF-01 |
| `POST /api/v1/google-chat/events` y `POST /chat` (existentes) | Sin cambios | Sin cambios. Siguen con el grafo sin la acción de derivar | RF-02 |

## Modules

### New: `src/services/http.py` (`outbound_client`)
| File | Responsibility | RF |
|---|---|---|
| `src/services/http.py` | `outbound_client(timeout: float) -> httpx.AsyncClient` con `httpx.AsyncHTTPTransport(local_address = "0.0.0.0", retries = 2)`. anyio toma la familia de la dirección local, así que solo resuelve y conecta destinos IPv4. httpcore reintenta solo `ConnectError`/`ConnectTimeout` al establecer la conexión, nunca una petición ya enviada (D9). | RF-16 a RF-18 |

### New: `src/agents/tools/handoff.py` (`derivar_a_marco`, `registrar_encargo`)
| File | Responsibility | RF |
|---|---|---|
| `src/agents/tools/handoff.py` | Tool `derivar_a_marco(motivo: str) -> str` con `@tool(parse_docstring = True)`, como `delegar_area`. Su docstring dice cuándo usarla (reuniones, precios, urgencias, sin información, el contacto insiste, encargos que no están entre los aceptables) y que el motivo va en una frase, en español y sin datos personales. No hace I/O: devuelve una guía para el modelo («Marco recibirá el aviso. Responde al contacto que lo revisarás y escribirás pronto, sin prometer plazos.»). El aviso real se publica después del turno (D2). | RF-01 |
| `src/agents/tools/handoff.py` | Tool `registrar_encargo(descripcion: str) -> str`: solo para encargos de los tipos aceptables que trae el prompt del turno. La descripción dice qué hay que hacer, en una frase y en español. Sin I/O: devuelve la guía «Marco recibirá el encargo. Confirma al contacto que lo harás, sin prometer plazos.» (D2, D13). | RF-19 |

### New: `src/services/whatsapp_handoff.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/whatsapp_handoff.py` | `digits(phone) -> str` y `is_family(phone, family_list) -> bool`: compara solo los dígitos (D16). `turn_instructions(session, phone) -> tuple[str, bool]`: lee `whatsapp_family_phones`, `whatsapp_family_instructions` y `whatsapp_accepted_tasks` (un valor por línea, sin líneas vacías) y devuelve las instrucciones del turno y si el contacto es familiar. A un familiar le da sus instrucciones y «Puedes aceptar cualquier encargo con registrar_encargo». A los demás, la sección «Encargos que puedes aceptar» o, si la lista falta o está vacía, «No aceptes encargos: derívalos con derivar_a_marco» (D14, D17). `tasks_of(messages) -> list[str]`: las `descripcion` de los `tool_calls` `registrar_encargo` del turno, sin repetidos y en orden (RF-24). `derivations_of(messages) -> list[str]`: los `motivo` de los `tool_calls` `derivar_a_marco` de los `AIMessage` del turno, sin repetidos y en orden (RF-06). `transcript(messages) -> str`: los últimos 20 `HumanMessage`/`AIMessage` con texto, como «Contacto:»/«Marco:», sin mensajes de tools. `summarize(llm, transcript) -> str \| None`: `SystemMessage(RESUMIR)` + `HumanMessage` con la frase fija de datos, dentro de `asyncio.timeout(RESUMEN_LIMITE = 20)`; ante cualquier error, `None` con log del tipo (D6). `notice_text(name, phone, motivos, summary, tasks, family) -> str`: plantilla fija en español con nombre o «Sin nombre», «(familia)» si corresponde, número y `https://wa.me/<número sin +>`; si hay motivos, la sección de derivación con el resumen (o «No se pudo resumir la conversación»); si hay encargos, la sección «Encargos» con uno por línea. Un turno con ambos da un solo texto (D11, D15). `publish_notice(session, text) -> bool`: lee `whatsapp_notify_space` (si falta: log y `False`), obtiene el token con `chat_token` y publica con `create_message` de `src/services/google_chat.py`, con `outbound_client`. Hasta 3 intentos con esperas de 1 y 2 s; devuelve si se publicó (D8). | RF-06, RF-10 a RF-15, RF-20 a RF-22, RF-24, RF-25, RF-27 a RF-33 |

### Changed
| File | Change | RF |
|---|---|---|
| `src/agents/coordinator.py` | `build_coordinator(tools) -> StateGraph` arma el grafo actual. `coordinator_builder = build_coordinator(TOOLS)` sigue con `[delegar_area]`, y `whatsapp_coordinator_builder = build_coordinator(WHATSAPP_TOOLS)` usa `[delegar_area, derivar_a_marco, registrar_encargo]`. El nodo `coordinator` hace `bind_tools` con las tools de su grafo (D1). `build_prompt` agrega `context.instructions` al final del prompt cuando no está vacío. | RF-01, RF-02, RF-19 a RF-21 |
| `src/agents/context.py` | `CoordinatorContext.instructions: str = ""`: instrucciones del turno que se suman al prompt del coordinador. | RF-20 |
| `src/services/assistant.py` | Nueva `run_turn(..., instructions = "") -> list[AnyMessage]`, con los mismos parámetros que `ask` más las instrucciones del turno. Le asigna un `id` (uuid) al `HumanMessage` del turno y devuelve solo los mensajes desde ese `id`. `ask` queda como envoltorio que devuelve `run_turn(...)[-1].text`, así `/chat` y Google Chat no cambian (D3). | RF-01, RF-03, RF-20 |
| `src/interfaces/whatsapp.py` | `InboundMessage.customer_profile: CustomerProfile \| None` (`name`). | RF-10 |
| `src/models/whatsapp.py` + migración | Columna `contact_name` en `WhatsappConversation`. | RF-10 |
| `src/services/whatsapp.py` | `add_inbound` recibe `contact_name` y lo actualiza si viene. `Batch.contact_name`. Nueva `pause_conversation(session, phone, now, window)`: `paused_until = next_pause(actual, now, window)` sin subir `epoch` ni guardar mensajes (D4). | RF-05, RF-10 |
| `src/services/whatsapp_reply.py` | `handle_event` pasa `customer_profile.name`. `answer_batch` lee `turn_instructions` con el número del contacto y llama a `run_turn` con esas instrucciones. Toma la respuesta del último mensaje, los motivos con `derivations_of` y los encargos con `tasks_of`. Tras la comparación de `epoch` (si cambió, no hay aviso, RF-07) envía la respuesta. Haya o no fallado el envío (RF-08), si hay motivos o encargos publica un solo aviso, y pausa solo si hubo derivación y el aviso se publicó (D5, D15). El cliente pasa a `outbound_client(20)`. | RF-03 a RF-09, RF-14, RF-16, RF-20, RF-23 a RF-26, RF-28, RF-30, RF-33 |
| `src/routers/whatsapp.py` y `main.py` | `main.py` compila también `app.state.whatsapp_graph = whatsapp_coordinator_builder.compile(checkpointer = checkpointer)` y `recover` usa ese grafo. El router pasa `request.app.state.whatsapp_graph` a `handle_event`. | RF-01, RF-02 |
| `src/services/google_chat_reply.py` y `src/routers/google_chat.py` | Los tres `httpx.AsyncClient(timeout = …)` pasan a `outbound_client(…)`, con el mismo timeout. | RF-16, RF-17 |

### Infrastructure
| File | Change |
|---|---|
| `alembic/versions/<rev>_whatsapp_contact_name.py` | `add_column` de `contact_name` (nullable); `downgrade` la borra. **Requiere aprobación de esquema** (aprobada con la spec). |
| `tests/http_test.py` | Red simulada: `httpcore.AsyncMockBackend` envuelto en un backend que falla `connect_tcp` N veces, instalado en el pool del transporte (U-N*). |
| `tests/whatsapp_handoff_test.py` | Motivos, transcripción, resumen y texto del aviso, y `publish_notice` con `get_property`/`chat_token`/`create_message` reemplazados (U-D*). |
| `tests/whatsapp_reply_test.py` | `FakeStore` con `contact_name` y `pause_conversation`; el fixture `channel` reemplaza `run_turn` y las funciones de `whatsapp_handoff`. Casos de derivación (U-R*). El grafo real se compila con el builder de WhatsApp (U-H3). U-W10 pasa a comprobar los dos grafos. |
| `tests/whatsapp_test.py` | El router entrega al grafo de WhatsApp (E6). |
| `README.md` | Properties `whatsapp_notify_space` (y cómo obtenerla), `whatsapp_accepted_tasks`, `whatsapp_family_phones` y `whatsapp_family_instructions` (con ejemplos); la derivación y los encargos en la sección «WhatsApp (YCloud)»; el SQL para sumar los criterios al prompt del «Agente WhatsApp»; nota de conexiones solo IPv4 con reintentos. |
| `pyproject.toml` / `uv.lock` | 3.5.0 → 3.6.0 y `uv sync`, en `chore(release): Bump version to v3.6.0`. |

## Data model

```python
class WhatsappConversation(Base):
  # … columnas de la spec 006 sin cambios …
  # Nombre de perfil de WhatsApp del contacto, para el aviso a Marco; se actualiza con cada mensaje entrante.
  contact_name: Mapped[str | None] = mapped_column(String(255))
```

- La migración solo añade una columna nullable: no reescribe filas y `downgrade` la elimina sin perder otros datos.
- Property nueva: `whatsapp_notify_space`, el nombre completo del space de Google Chat (`spaces/AAAA…`). Si falta, no hay aviso ni pausa (RF-14).
- Property nueva: `whatsapp_accepted_tasks`, los tipos de encargo que el asistente puede aceptar, uno por línea (por ejemplo «Enviar mi CV o mi portafolio»). Si falta o está vacía, no acepta ninguno (RF-21).
- Properties nuevas: `whatsapp_family_phones` (un número por línea, en cualquier formato) y `whatsapp_family_instructions` (texto libre con el trato familiar). Si falta la lista, nadie es familiar (RF-32).
- Dato nuevo: los criterios de derivación y de encargos y el trato profesional se suman al prompt del «Agente WhatsApp» (`agents`, id 34 en la BD remota) con un `UPDATE` documentado en el README. El `UPDATE` reemplaza la regla actual «no aceptes encargos».

## Decisions
| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Un grafo compilado aparte para WhatsApp, con `[delegar_area, derivar_a_marco, registrar_encargo]`, construido por la misma función que el grafo actual | Un solo grafo que elija las tools según el contexto | `ToolNode` fija sus tools al construir el grafo. Con un grafo aparte, `/chat` y Google Chat no ven la acción ni por error (RF-02) y su grafo no cambia. |
| D2 | La tool solo deja constancia en el turno; el aviso lo publica `answer_batch` después de enviar la respuesta, leyendo los `tool_calls` | Publicar el aviso dentro de la tool | Dentro de la tool, el aviso saldría antes de saber si la respuesta se descarta por un echo (RF-07), y dos llamadas en un turno darían dos avisos (RF-06). |
| D3 | `run_turn` devuelve los mensajes del turno, cortando desde un `id` propio del `HumanMessage`; `ask` queda como envoltorio | Una lista mutable en `CoordinatorContext` que la tool llene | La lista exigiría que la tool conozca un contexto propio de WhatsApp y que se comparta mutable entre tools. Leer los `tool_calls` usa lo que el grafo ya guarda y se prueba con el grafo real (U-H3). |
| D4 | `pause_conversation` usa `next_pause` sin subir `epoch` | Reutilizar `add_echo` | `add_echo` sube `epoch`, guardaría un mensaje falso de Marco en el historial y descartaría el lote siguiente. La derivación solo debe pausar (RF-05). |
| D5 | Orden: `epoch` → envío → aviso → pausa si se publicó → marca de leído | Avisar antes de enviar la respuesta | Si Marco escribió durante el lote no hay aviso (RF-07). Si YCloud rechaza la respuesta, Marco igual debe enterarse (RF-08). Sin aviso, no hay pausa (RF-14). |
| D6 | Resumen a partir del estado del grafo (últimos 20 mensajes sin tools), con instrucción fija en código y límite propio de 20 s | Usar las filas de la cola, o `whatsapp_timeout` | Las filas se borran al terminar el lote y no tienen las respuestas del asistente. El lote ya consumió parte de `whatsapp_timeout`, así que el resumen necesita su propio límite (RF-12). La frase de datos sigue el criterio de `TRANSCRIBE` y `AVISO_DATOS`. |
| D7 | Guardar `contact_name` en la conversación con cada entrante | Consultar la API de contactos de YCloud al avisar | Una llamada extra al avisar puede fallar justo cuando se necesita el aviso (RF-13, RF-14). Una columna nullable cuesta una migración trivial. |
| D8 | 3 intentos de publicar el aviso, con esperas de 1 y 2 s, dentro de la misma tarea del lote | Una cola de avisos pendientes con reintentos en segundo plano | El usuario descartó los reintentos en segundo plano. Como mucho suma ~3 s más los timeouts del cliente a un lote que ya respondió. |
| D9 | Un único `outbound_client` con `local_address = "0.0.0.0"` y `retries = 2`, usado por los cuatro clientes y el aviso | Desactivar IPv6 en el pod o en el cluster | Cambiar la red del cluster está fuera de alcance. Con la dirección local IPv4, anyio no intenta destinos IPv6 (RF-16). Los reintentos de httpcore solo cubren la conexión, así que no duplican mensajes (RF-17, RF-18). |
| D10 | `whatsapp_notify_space` guarda el nombre del space (`spaces/…`), que se saca de la URL del mensaje directo con la app en Google Chat | Guardar el correo de Marco y buscar el DM con `spaces.findDirectMessage` | Sería una llamada más por aviso y depende de un permiso de la API que no se usa hoy. `create_message` ya publica en un space por nombre. |
| D11 | Plantilla fija en código; solo el resumen lo redacta el LLM | Que el LLM redacte el aviso completo | Nombre, número, motivos y enlace deben aparecer siempre y exactos (RF-10). El resumen es la única parte que puede faltar (RF-12). |
| D12 | Los criterios para derivar viven en el prompt del «Agente WhatsApp» y en la docstring de la tool | Reglas fijas en código (palabras clave) | Lo pide la spec: el prompt se edita sin redeploy, y las palabras clave fallan con pedidos indirectos («¿te tinca un café el jueves?»). |
| D13 | Una tool `registrar_encargo` separada de `derivar_a_marco` | Una sola tool `avisar_a_marco(tipo, texto)` | Derivar pausa y encargar no (RF-23). Con dos tools, el modelo elige por el nombre y la docstring, y el código decide la pausa sin interpretar un campo de texto. |
| D14 | Los encargos aceptables se leen de una property en cada turno y se suman al prompt con `CoordinatorContext.instructions` | Escribirlos dentro del prompt del «Agente WhatsApp» | Editar una lista corta es más simple y seguro que editar el prompt completo. Con la property vacía, el código pone «no aceptes encargos», así que el comportamiento por defecto es seguro aunque el prompt no lo diga (RF-20, RF-21). |
| D15 | Un solo aviso por turno, con una sección por motivo de derivación y otra por encargos | Un aviso por cada encargo y otro por la derivación | Varios avisos del mismo chat en segundos son ruido, y la spec pide uno solo (RF-24, RF-25). La pausa depende solo de que haya derivación (RF-23). |
| D16 | Comparar los números solo por sus dígitos | Comparar el texto exacto en E.164 | YCloud envía `+569…` y es fácil cargar la lista con espacios o sin `+`; con texto exacto, un familiar mal escrito recibiría trato profesional sin aviso (RF-27). |
| D17 | Las instrucciones familiares se suman al prompt común con `CoordinatorContext.instructions`, el mismo mecanismo que los encargos | Un coordinador aparte para la familia | El usuario lo eligió así. Las reglas de seguridad, derivación y encargos quedan en un solo prompt y no se desincronizan (RF-28 a RF-30). |

## RF coverage
| RF | Where | Tests |
|---|---|---|
| RF-01, RF-02 | `coordinator.py` (dos grafos), `handoff.py`, `main.py`, router | U-R1, U-H3, E6 |
| RF-03, RF-04, RF-05 | `answer_batch`, `publish_notice`, `pause_conversation` | U-R2 |
| RF-06 | `derivations_of` | U-D1, U-R3 |
| RF-07 | `answer_batch` (comparación de `epoch`) | U-R4 |
| RF-08 | `answer_batch` | U-R5 |
| RF-09 | `handle_event` sin cambios en el echo | U-R6 |
| RF-10 | `notice_text`, `contact_name` | U-D3, U-D4, U-R7 |
| RF-11 | `transcript`, `summarize` | U-D2, U-D5 |
| RF-12 | `summarize`, `notice_text` | U-D6 |
| RF-13 | `publish_notice` | U-D7 |
| RF-14 | `publish_notice`, `answer_batch` | U-D8, U-D9, U-R8 |
| RF-15 | `publish_notice` | U-D10 |
| RF-16 | `outbound_client` y sus cuatro usos | U-N1, U-N4 |
| RF-17 | `outbound_client` | U-N2, U-N3 |
| RF-18 | `outbound_client` | U-N5 |
| RF-19 | `registrar_encargo`, grafo de WhatsApp | U-R1, U-H4 |
| RF-20 | `turn_instructions`, `build_prompt`, `run_turn` | U-D11, U-P1, U-R9 |
| RF-21 | `turn_instructions` | U-D12 |
| RF-22 | `notice_text`, `tasks_of` | U-D13, U-R10 |
| RF-23 | `answer_batch` | U-R10 |
| RF-24 | `tasks_of` | U-D14, U-R11 |
| RF-25 | `notice_text`, `answer_batch` | U-D15, U-R12 |
| RF-26 | `answer_batch` | U-R13 |
| RF-27 | `is_family`, `digits` | U-D16 |
| RF-28, RF-29 | `turn_instructions` | U-D17, U-R14 |
| RF-30 | `turn_instructions` | U-D18, U-R15 |
| RF-31 | `turn_instructions` | U-D19 |
| RF-32 | `turn_instructions` | U-D20 |
| RF-33 | `notice_text`, `answer_batch` | U-D21, U-R14 |

## Test strategy

**Unit — red (`tests/http_test.py`)** con un backend simulado de `httpcore` instalado en el pool del transporte:
- U-N1 `outbound_client` crea el transporte con dirección local `0.0.0.0` y `retries = 2`.
- U-N2 Dos `ConnectError` al conectar y luego éxito: la respuesta llega, con 3 intentos de conexión y una sola petición.
- U-N3 Tres `ConnectError` seguidos: lanza `httpx.ConnectError` tras 3 intentos.
- U-N4 Ningún módulo de `src/` crea `httpx.AsyncClient(` fuera de `src/services/http.py` (test de guarda sobre el código fuente).
- U-N5 La conexión se establece y el servidor corta después de recibir la petición: no se reintenta (1 conexión) y lanza error.

**Unit — aviso (`tests/whatsapp_handoff_test.py`)**
- U-D1 `derivations_of`: dos `tool_calls` con motivos distintos y uno repetido dan dos motivos, en orden; sin `tool_calls`, lista vacía.
- U-D2 `transcript` etiqueta «Contacto:»/«Marco:», omite los mensajes de tools y se queda con los últimos 20.
- U-D3 `notice_text` incluye nombre, número, motivos, resumen y `https://wa.me/56911111111` (sin `+`).
- U-D4 Sin nombre: «Sin nombre».
- U-D5 `summarize` envía la instrucción fija y la transcripción marcada como datos, y devuelve el texto del modelo.
- U-D6 `summarize` con error o bloqueado más que el límite devuelve `None`, y `notice_text` pone «No se pudo resumir la conversación».
- U-D7 `create_message` falla dos veces y luego publica: 3 intentos y `True`.
- U-D8 Falla tres veces: `False`, log de error sin el texto del aviso (`caplog`).
- U-D9 Sin `whatsapp_notify_space`, o sin token: `False` sin llamar a `create_message`.
- U-D10 Se lee `whatsapp_notify_space` en cada llamada: dos llamadas con valores distintos publican en spaces distintos.
- U-D11 `turn_instructions` para un no familiar con dos tipos (y una línea vacía entre medio) arma la sección con los dos, uno por línea.
- U-D12 Sin `whatsapp_accepted_tasks`, o vacía, un no familiar recibe la instrucción de no aceptar encargos y derivarlos.
- U-D13 `notice_text` solo con encargos: descripción, nombre, número y `wa.me`, sin sección de derivación ni resumen.
- U-D14 `tasks_of`: dos encargos distintos y uno repetido dan dos, en orden.
- U-D15 `notice_text` con motivos y encargos: un texto con las dos secciones y el resumen.
- U-D16 `is_family` reconoce `+56 9 1111 1111`, `56911111111` y `+56911111111` como el mismo número, y no reconoce otro.
- U-D17 Familiar: `turn_instructions` devuelve las instrucciones familiares y «cualquier encargo», sin la lista de aceptables, y `True`.
- U-D18 No familiar: sin las instrucciones familiares y `False`.
- U-D19 Las properties se leen en cada llamada: dos llamadas con valores distintos dan resultados distintos.
- U-D20 Sin `whatsapp_family_phones`, o vacía, nadie es familiar.
- U-D21 `notice_text` de un familiar incluye «(familia)»; el de un no familiar, no.

**Unit — integración con el canal (`tests/whatsapp_reply_test.py`)** con `FakeStore`, `run_turn` falso que devuelve mensajes con o sin `tool_calls`, YCloud falso y `publish_notice`/`summarize` reemplazados:
- U-R1 El grafo de WhatsApp expone `delegar_area`, `derivar_a_marco` y `registrar_encargo`; el grafo general solo `delegar_area` (reemplaza U-W10).
- U-R2 Turno con derivación: se envía la respuesta, se publica un aviso con el motivo y la conversación queda en pausa 12 h.
- U-R3 Dos derivaciones en el mismo turno: un solo aviso con los dos motivos.
- U-R4 Echo durante el turno con derivación: sin envío y sin aviso.
- U-R5 YCloud rechaza la respuesta: el aviso se publica igual.
- U-R6 Un echo pausa sin publicar aviso.
- U-R7 El nombre de perfil del entrante llega al aviso.
- U-R8 `publish_notice` devuelve `False`: no hay pausa y el siguiente mensaje del contacto se responde.
- U-R9 `run_turn` recibe las instrucciones de encargos leídas en ese turno.
- U-R10 Turno con un encargo: se envía la respuesta, se publica un aviso con el encargo y la conversación **no** queda en pausa.
- U-R11 Dos encargos en el mismo turno: un solo aviso con los dos.
- U-R12 Derivación y encargo en el mismo turno: un solo aviso con ambos, y pausa.
- U-R13 Aviso de encargos que no se publica: log con el número del contacto y sin la descripción (`caplog`), sin pausa.
- U-R14 Contacto familiar: `run_turn` recibe las instrucciones familiares y el aviso de su encargo dice «(familia)».
- U-R15 Contacto no familiar: `run_turn` no recibe las instrucciones familiares.

**Unit — prompt (`tests/whatsapp_reply_test.py`)**
- U-P1 `build_prompt` agrega `context.instructions` al final; sin instrucciones, el prompt es igual al actual.

**Unit — grafo real (`tests/whatsapp_reply_test.py`)**
- U-H3 Con un modelo falso que primero llama a `derivar_a_marco` y luego responde, `run_turn` sobre el grafo de WhatsApp devuelve los mensajes del turno (AI con `tool_calls`, ToolMessage con la guía y AI final) y no los de turnos anteriores.
- U-H4 Igual con `registrar_encargo`: la ToolMessage trae la guía del encargo y `tasks_of` sobre el turno devuelve la descripción.

**E2E (`tests/whatsapp_test.py`)**
- E6 Un evento firmado llega a `handle_event` con `app.state.whatsapp_graph`, no con `app.state.graph`.

**Limitaciones.**
- El backend simulado prueba los reintentos, pero no que anyio filtre IPv6: eso se cubre con U-N1 (la dirección local es IPv4), la lectura del código de anyio y la verificación manual en el pod.
- La publicación real en Google Chat, el formato del space y que el modelo use la tool en los casos esperados se verifican a mano.

## Implementation order
1. Modelo, migración y `alembic check` (con la aprobación de esquema).
2. `src/services/http.py` con U-N1 a U-N3 y U-N5; reemplazar los cuatro clientes con U-N4.
3. Tool `derivar_a_marco`, `build_coordinator` y los dos grafos; `run_turn`; U-H3 y U-R1.
4. `src/services/whatsapp_handoff.py` con U-D*.
5. `contact_name`, `pause_conversation` y la integración en `answer_batch` y `handle_event`, con U-R2 a U-R8.
6. `main.py` y el router con el grafo de WhatsApp (E6).
7. README.
8. Versión 3.6.0 y `uv sync`.

## Release
1. Aplicar la migración en la BD remota antes del merge (sin efecto en 3.5.0, que no lee la columna) o dejar que `entrypoint.sh` la aplique al desplegar.
2. Merge a `main`: `release.yaml` despliega en el namespace `virtual-assistant`.
3. En Google Chat, abrir el mensaje directo con la app y copiar el id de la URL (`…/chat/dm/<id>`), luego `INSERT` de `whatsapp_notify_space = 'spaces/<id>'`.
4. Cargar `whatsapp_accepted_tasks` con los tipos de encargo que Marco define, `whatsapp_family_phones` y `whatsapp_family_instructions`, y sumar los criterios de derivación y de encargos al prompt del «Agente WhatsApp» con el `UPDATE` del README, revisado por Marco.
5. Verificación manual de la spec desde otro teléfono. Además, en el pod, confirmar que una petición con `outbound_client` a `api.ycloud.com` conecta por una IP v4.

## Risks
- **El modelo no deriva cuando debería, o deriva de más:** se calibra con el prompt sin redeploy. En la prueba manual se revisa un caso de reunión, uno de precio y uno de pregunta común.
- **El id del space sacado de la URL es incorrecto:** la publicación falla, aplica RF-14 (sin pausa) y queda en el log. El README da un ejemplo del formato.
- **Atributos privados en U-N1:** `outbound_client` se inspecciona en `_pool` y puede romperse con una actualización de httpx. Si pasa, se ajusta el test, no el comportamiento; U-N2 y U-N3 siguen probando los reintentos.
- **Un encargo aceptado cuyo aviso falla:** el contacto ya recibió «lo hago» y Marco no se entera. Se mitiga con los reintentos y el log (RF-26). Si pasa a menudo, la cola de avisos descartada en la spec vuelve a evaluarse.
- **El modelo acepta encargos fuera de la lista:** la lista va en el prompt de cada turno y la docstring de `registrar_encargo` lo restringe. En la prueba manual se pide uno fuera de la lista y se comprueba que lo deriva.
- **La cuenta de un familiar comprometida pide dinero:** como encargo libre, el asistente lo acepta y avisa a Marco, pero nunca paga ni comparte códigos (reglas de seguridad). Marco decide al leer el aviso.
- **Latencia extra del aviso:** el resumen (≤ 20 s) y los reintentos (≤ ~3 s) ocurren después de enviar la respuesta, así que no retrasan al contacto. Sí ocupan la conversación durante ese tiempo.
