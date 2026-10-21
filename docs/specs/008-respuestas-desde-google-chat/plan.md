# Plan 008 — Consultas a Marco y respuestas desde Google Chat en WhatsApp

- **Spec:** [spec.md](spec.md) (approved 2026-10-21)
- **Status:** Approved
- **Version:** 3.7.0 (feat → minor, desde 3.6.0)

El plan respeta la constitución:
- §2: el router de Google Chat solo decide a qué servicio entregar el evento. La búsqueda del aviso, la redacción y el envío viven en `services`.
- §4: los tests corren sin BD real, sin LLM, sin YCloud y sin Google, con los fakes de `tests/whatsapp_reply_test.py` y `tests/google_chat_test.py`.
- §6: no hay properties nuevas; se reutilizan `whatsapp_enabled`, `whatsapp_notify_space` y las de la familia.
- §7: Marco recibe en el hilo mensajes en español, sin trazas, y el log no guarda su texto ni el mensaje enviado.

No agrega dependencias. **Requiere aprobación de esquema:** la tabla `whatsapp_notices` y la columna `last_inbound_at` en `whatsapp_conversations`, con su migración (ver **Data model**). El usuario aprobó la spec junto con el posible cambio de esquema y con los cambios a las specs 001 y 007. El prompt del «Agente WhatsApp» sin frases literales es un dato: el `UPDATE` va en el README y lo revisa Marco.

## Endpoints

| Método y ruta | Auth | Respuesta | RF |
|---|---|---|---|
| `POST /api/v1/google-chat/events` (existente) | ID token de Google, sin cambios | Si el mensaje está en el hilo de un aviso de WhatsApp, responde en el hilo con el resultado del envío (texto enviado, ventana cerrada, canal apagado, sin texto o no se pudo enviar). En cualquier otro caso, igual que hoy, con el «Agente Interno» | RF-12 a RF-14, RF-17, RF-19 a RF-23 |
| `POST /api/v1/whatsapp/events` (existente) | `YCloud-Signature`, sin cambios | Sin cambios. El grafo de WhatsApp suma la acción de consultar | RF-01 |

## Modules

### New: `src/services/whatsapp_relay.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/whatsapp_relay.py` | `relay_reply(graph, session, event) -> str \| None` atiende la respuesta desde el hilo:<br>1. Toma `message.thread.name`. Si no hay hilo o `find_notice` no lo conoce, devuelve `None` y el router sigue con el «Agente Interno» (RF-13).<br>2. Comprueba `channel_enabled`, el texto del mensaje (`message_text`) y la ventana de 24 h (`last_inbound_at`), y responde el texto fijo de cada caso.<br>3. Redacta con `compose_reply`, envía con `send_text`/`split_text` y `outbound_client`, guarda el mensaje con `add_marco_reply` y devuelve «Enviado a <nombre>:\n<texto>».<br>Cualquier fallo de redacción o de envío devuelve «No se pudo enviar…» sin tocar el historial ni la pausa; el log guarda solo el tipo de error (RF-24).<br>`compose_reply(llm, conversation, reply, tone) -> str`: `SystemMessage(REDACTAR + tono)` y `HumanMessage` con la conversación y la respuesta de Marco entre marcas, dentro de `asyncio.timeout(REDACCION_LIMITE = 15)` (D7, D8). | RF-12, RF-14 a RF-17, RF-19 a RF-24 |

### Changed
| File | Change | RF |
|---|---|---|
| `src/agents/tools/handoff.py` | Nueva tool `consultar_a_marco(pregunta: str)`, para preguntas que el asistente no sabe responder, con la pregunta autocontenida y sin datos personales (D10). `derivar_a_marco` saca de su docstring «pregunte algo que no está en la información disponible». `GUIA_DERIVACION`, `GUIA_ENCARGO` y la nueva `GUIA_CONSULTA` describen el criterio sin frases entre comillas: responder con sus palabras, según lo que pidió el contacto, sin prometer plazos ni confirmar lo que Marco debe decidir (D11). | RF-01, RF-09, RF-10 |
| `src/agents/coordinator.py` | `WHATSAPP_TOOLS` suma `consultar_a_marco`; `TOOLS` no cambia. | RF-01, RF-02 |
| `src/services/whatsapp_handoff.py` | `questions_of(messages)` lee las `pregunta` de los `tool_calls` (con `tool_args`).<br>`notice_text(..., questions)` suma la sección «Te consulta:» y siempre cierra con la línea fija `RESPONDER = "Responde en este hilo para contestarle por WhatsApp."` (D13).<br>`publish_notice` devuelve el nombre del hilo publicado (`str \| None`) en vez de `bool` (D2).<br>`family_tone(session, phone) -> str \| None` sale de `turn_instructions` para que la redacción use el mismo trato familiar (D8). | RF-04, RF-06, RF-11 |
| `src/services/google_chat.py` | `create_message` devuelve `response.json()["thread"]["name"]` (o `None` si no viene). `deliver` ignora el valor (D2). | RF-11, RF-18 |
| `src/services/whatsapp.py` | `add_inbound` actualiza `last_inbound_at` con el mayor `sent_at`, también durante la pausa (D4).<br>Nuevas `save_notice(session, thread, phone, now)`, `find_notice(session, thread) -> NoticeTarget \| None` (teléfono, número del negocio, nombre y `last_inbound_at`) y `add_marco_reply(session, phone, message)`. Esta última guarda el mensaje como `echo` en `history` y deja `paused_until = None`, sin subir `epoch` (D5, D6).<br>`channel_enabled` pasa aquí desde `src/routers/whatsapp.py`, que la importa (D14). | RF-15, RF-16, RF-18, RF-19, RF-23 |
| `src/services/whatsapp_reply.py` | `answer_batch` lee también `questions_of(turn)`. Una consulta publica aviso igual que un encargo, en el mismo aviso único, y pausa solo si hubo derivación (RF-05, RF-06). `notify` guarda el hilo con `save_notice` cuando se publicó, y el log de un aviso fallido conserva el número sin el contenido (RF-08). | RF-03 a RF-08 |
| `src/routers/google_chat.py` | Antes de `answer`, llama a `relay_reply(request.app.state.whatsapp_graph, session, event)`; si devuelve texto, responde `chat_reply(texto)` en el hilo (D9). | RF-12, RF-13 |

### Infrastructure
| File | Change |
|---|---|
| `src/models/whatsapp.py` + `alembic/versions/<rev>_whatsapp_notices.py` | Tabla `whatsapp_notices` y columna `last_inbound_at`; `downgrade` las elimina. **Requiere aprobación de esquema** (aprobada con la spec). |
| `tests/whatsapp_relay_test.py` | Respuestas desde el hilo con `find_notice`, `add_marco_reply`, el LLM, YCloud y las properties reemplazados (U-C*). |
| `tests/whatsapp_handoff_test.py` | Consultas, línea de respuesta y guías sin frases literales (U-Q*). |
| `tests/whatsapp_reply_test.py` | `FakeStore` con `last_inbound_at`, `save_notice` y `add_marco_reply`; `FakeHandoff.publish_notice` devuelve un hilo; casos de consulta (U-R16 a U-R21). |
| `tests/google_chat_test.py` | `create_message` devuelve el hilo; el endpoint entrega al relay o al «Agente Interno» (E7, E8). |
| `README.md` | Consultas, cómo responder desde el hilo, ventana de 24 h, la tabla nueva y el `UPDATE` del prompt sin frases literales. |
| `pyproject.toml` / `uv.lock` | 3.6.0 → 3.7.0 y `uv sync`, en `chore(release): Bump version to v3.7.0`. |

## Data model

```python
class WhatsappConversation(Base):
  # … columnas de las specs 006 y 007 sin cambios …
  # Último mensaje del contacto: WhatsApp solo permite texto libre por la API hasta 24 h después (RF-19).
  last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))

class WhatsappNotice(Base):
  __tablename__ = "whatsapp_notices"

  # Hilo de Google Chat del aviso (spaces/…/threads/…): una respuesta en él va al contacto (D1, D3).
  thread: Mapped[str] = mapped_column(String(255), primary_key = True)
  phone: Mapped[str] = mapped_column(ForeignKey("whatsapp_conversations.phone", ondelete = "CASCADE"), index = True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())
```

- Una conversación tiene muchos avisos; cada aviso, un solo hilo. Borrar la conversación borra sus avisos en cascada.
- La migración solo añade: una tabla nueva y una columna nullable, sin reescribir filas. `downgrade` borra ambas.
- Las conversaciones existentes quedan con `last_inbound_at` nulo, que cuenta como ventana cerrada hasta el próximo mensaje del contacto (D12).
- Los avisos publicados antes de la migración no tienen fila, así que sus hilos siguen yendo al «Agente Interno».
- Sin vencimiento de filas: un aviso por turno con acción es poco volumen. Limpiar avisos viejos queda fuera de alcance.
- Dato: el `UPDATE` del prompt del «Agente WhatsApp» reemplaza las frases de ejemplo literales y la regla de derivar lo desconocido por la de consultar.

## Decisions
| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Reconocer la respuesta por el nombre del hilo de Google Chat, guardado al publicar el aviso | Leer el número del contacto del texto citado o del enlace `wa.me` del aviso | El evento trae `message.thread.name` y el texto citado no viene en el evento. El hilo no se puede falsificar escribiendo un número (RF-12, RF-13). |
| D2 | `create_message` devuelve el hilo de su respuesta y `publish_notice` lo propaga | Listar los mensajes del space después de publicar | La respuesta de `spaces.messages.create` ya trae `thread.name`. Listar mensajes pide otro permiso de la API y una llamada más. |
| D3 | Tabla `whatsapp_notices` (un hilo por fila) | Una columna `last_notice_thread` en la conversación | Todos los avisos de un contacto deben aceptar respuestas, no solo el último (RF-17), también después de un reinicio (RF-18). |
| D4 | Columna `last_inbound_at`, actualizada en `add_inbound` | Consultar a YCloud la fecha del último mensaje | Las filas de `whatsapp_messages` se borran al terminar el lote, y una llamada extra a YCloud puede fallar justo al responder. |
| D5 | El mensaje enviado se guarda como `echo` con estado `history` y entra al historial en el próximo lote, como `previous` | Escribirlo en el checkpointer con `aupdate_state` | Lo dice la D4 de la spec 006: solo el lote tomado escribe en el hilo del checkpointer. Escribir en paralelo a un turno en curso puede perder una de las dos escrituras. `history_message` ya convierte un `echo` en mensaje de Marco (RF-15). |
| D6 | `add_marco_reply` levanta la pausa y no sube `epoch` | Tratarlo como un echo, que sube `epoch` y descarta el lote en curso | El usuario eligió que el asistente se reanude (RF-16). Descartar el lote dejaría sin respuesta los mensajes que el contacto mandó mientras tanto. |
| D7 | Respuesta síncrona en el hilo, con límite propio de redacción `REDACCION_LIMITE = 15` s | Reutilizar la respuesta diferida de `answer`/`deliver` | `deliver` publica fuera del hilo y está atado al grafo del «Agente Interno». 15 s de redacción más ~10 s de envío caben en los 30 s que espera Google Chat (RF-14, RF-20). |
| D8 | Redactar con una instrucción fija en código, el trato familiar o profesional y los últimos 20 mensajes (`transcript`) | Pasar la respuesta por el grafo del «Agente WhatsApp» | El grafo trae tools y reglas que aquí no aplican, y podría derivar o consultar sobre la respuesta de Marco. Con la instrucción fija, el mensaje no agrega ni quita datos (RF-12). |
| D9 | El router de Google Chat llama a `relay_reply` antes de `answer`; un `None` sigue el camino actual | Un endpoint aparte para las respuestas | El complemento de Google Chat tiene un solo endpoint de eventos. El chequeo cuesta una consulta por índice y solo aplica cuando el mensaje trae hilo (RF-13). |
| D10 | Tool `consultar_a_marco` separada de `derivar_a_marco` | Un parámetro de `derivar_a_marco` que indique si pausa | Sigue la D13 de la spec 007: el modelo elige por el nombre y la docstring, y el código decide la pausa sin interpretar argumentos (RF-05). |
| D11 | Guías de las tools sin frases entre comillas, con una prueba de guarda | Cambiar solo el prompt | La guía es lo último que lee el modelo antes de responder, y hoy dicta la frase (RF-09). La prueba evita que una frase vuelva a colarse. |
| D12 | `last_inbound_at` nulo cuenta como ventana cerrada | Suponer la ventana abierta | Un envío fuera de la ventana falla en YCloud. Cerrada por defecto, Marco recibe el enlace `wa.me` y responde desde el celular (RF-19). |
| D13 | La línea «Responde en este hilo…» es un texto fijo del aviso | Que la agregue el LLM en el resumen | El resumen puede faltar (RF-12 de la spec 007), y la indicación debe estar en todos los avisos (RF-11). |
| D14 | `channel_enabled` pasa a `src/services/whatsapp.py` y la usan el webhook y el relay | Duplicarla en el relay | Un solo criterio para «canal apagado» (RF-23). El router de WhatsApp no ejecuta SQL, como pide la constitución §2. |

## RF coverage
| RF | Where | Tests |
|---|---|---|
| RF-01, RF-02 | `handoff.py`, `WHATSAPP_TOOLS` | U-Q1 |
| RF-03, RF-04 | `answer_batch`, `notice_text` | U-Q2, U-R16 |
| RF-05 | `answer_batch` | U-R16 |
| RF-06 | `answer_batch`, `notice_text` | U-Q3, U-R17 |
| RF-07 | `answer_batch` (comparación de `epoch`) | U-R18 |
| RF-08 | `notify` | U-R19 |
| RF-09, RF-10 | `GUIA_*`, docstrings | U-Q4, U-Q5 |
| RF-11 | `notice_text` (`RESPONDER`) | U-Q6 |
| RF-12 | `relay_reply`, `compose_reply` | U-C1, U-C2, E7 |
| RF-13 | router, `relay_reply` | U-C3, E8 |
| RF-14 | `relay_reply` | U-C1 |
| RF-15, RF-16 | `add_marco_reply` | U-C4, U-R20 |
| RF-17 | `relay_reply` | U-C5 |
| RF-18 | `publish_notice`, `save_notice`, `find_notice` | U-Q7, U-R21, U-C6 |
| RF-19 | `relay_reply`, `last_inbound_at` | U-C7, U-R22 |
| RF-20 | `compose_reply` | U-C8 |
| RF-21 | `relay_reply` | U-C9 |
| RF-22 | `relay_reply` | U-C10 |
| RF-23 | `relay_reply`, `channel_enabled` | U-C11 |
| RF-24 | `relay_reply` | U-C8, U-C9 |

## Test strategy

**Unit — consultas, aviso y guías (`tests/whatsapp_handoff_test.py`)**
- U-Q1 El grafo de WhatsApp expone `consultar_a_marco` además de las tres tools actuales, y el grafo general solo `delegar_area` (amplía U-R1).
- U-Q2 `questions_of` junta las preguntas sin repetir; `notice_text` con preguntas trae nombre, «(familia)» si corresponde, número, `wa.me` y la sección «Te consulta:», sin resumen.
- U-Q3 `notice_text` con preguntas, motivos y encargos da un solo texto con las tres secciones.
- U-Q4 `GUIA_DERIVACION`, `GUIA_ENCARGO` y `GUIA_CONSULTA` no traen comillas (`«`, `»`, `"`) y dicen que responda con sus propias palabras, sin prometer plazos.
- U-Q5 La docstring de `derivar_a_marco` ya no menciona la información que falta, y la de `consultar_a_marco` sí.
- U-Q6 Todo aviso (consulta, derivación o solo encargos) termina con `RESPONDER`.
- U-Q7 `publish_notice` devuelve el hilo que devolvió `create_message`, y `None` si falla tras 3 intentos, si falta el space o si falta el token (ajusta U-D7 a U-D10).

**Unit — canal (`tests/whatsapp_reply_test.py`)**
- U-R16 Turno con una consulta: se envía la respuesta, se publica un aviso con la pregunta, se guarda su hilo y la conversación **no** queda en pausa.
- U-R17 Consulta y derivación en el mismo turno: un solo aviso con ambas, y pausa.
- U-R18 Echo durante un turno con consulta: sin envío ni aviso.
- U-R19 Aviso de consulta que no se publica: log con el número y sin la pregunta (`caplog`), sin hilo guardado.
- U-R20 `add_marco_reply` en el `FakeStore`: el mensaje queda como `echo` en `history`, la pausa se levanta y `epoch` no cambia. El siguiente lote lo recibe como mensaje de Marco en `previous`.
- U-R21 El hilo se guarda también en avisos de derivación y de encargos.
- U-R22 `add_inbound` actualiza `last_inbound_at` con el mayor `sent_at`, también durante la pausa.

**Unit — respuestas desde el hilo (`tests/whatsapp_relay_test.py`)** con un evento de Google Chat en el hilo de un aviso conocido, y con LLM, YCloud, `find_notice`, `add_marco_reply` y las properties reemplazados:
- U-C1 El texto de Marco se redacta y se envía al contacto desde el número del negocio, y la respuesta es «Enviado a Sara:» con el texto enviado.
- U-C2 `compose_reply` manda la instrucción fija, el tono (familiar o profesional), la conversación y la respuesta de Marco entre marcas.
- U-C3 Un mensaje sin hilo, o en un hilo que no es de un aviso, devuelve `None` sin leer properties ni llamar al LLM.
- U-C4 Tras enviar se llama a `add_marco_reply` con el texto enviado, no con el de Marco.
- U-C5 Dos mensajes en el mismo hilo se redactan y envían por separado.
- U-C6 El aviso se busca por el nombre del hilo en cada evento, sin caché: con el `FakeStore` recreado (simula un reinicio) se sigue encontrando.
- U-C7 Con `last_inbound_at` de hace 25 h, o nulo, no se envía y la respuesta trae «ventana» y `https://wa.me/56911111111`.
- U-C8 El LLM falla o se bloquea más que el límite: no se envía, no se llama a `add_marco_reply` y la respuesta es «No se pudo enviar…». El log no trae el texto de Marco.
- U-C9 YCloud responde 400 o `ConnectError`: «No se pudo enviar…», sin `add_marco_reply`, y el log sin el texto.
- U-C10 Mensaje sin texto (solo adjunto): no se envía y la respuesta dice que solo se envía texto.
- U-C11 Con `whatsapp_enabled` en `false` o ausente: no se envía y la respuesta dice que el canal está apagado.

**E2E (`tests/google_chat_test.py`)**
- E7 Un evento firmado en el hilo de un aviso responde en el hilo con el texto de `relay_reply` y no llama a `answer`.
- E8 Si `relay_reply` devuelve `None`, el evento va a `answer` como hoy.
- `test_create_message_publica_en_el_space_sin_hilo` pasa a comprobar también que devuelve `thread.name`.

**Limitaciones.**
- Que Google Chat permita responder en el hilo de un mensaje de la app dentro del DM, y que el evento traiga ese hilo, no se puede probar con fakes. Se comprueba a mano antes de implementar (paso 1 del orden).
- El SQL nuevo (`save_notice`, `find_notice`, `add_marco_reply` y `last_inbound_at`) se prueba con un script contra la BD local, como en la spec 007.
- La naturalidad de las respuestas se juzga en la verificación manual.

## Implementation order
1. **Comprobación manual de los hilos** (gate): Marco responde en el hilo del aviso «[PRUEBA]» ya publicado en `spaces/g4uwxqAAAAE`. Hoy ese mensaje va al «Agente Interno», que responde de forma síncrona en el hilo del evento. Si su respuesta aparece dentro del hilo del aviso, Google Chat permite el hilo en el DM y el evento trae el hilo del aviso. Si no se puede responder en el hilo, o la respuesta aparece fuera, el plan vuelve a decidirse.
2. Modelo, migración y `alembic check` (con la aprobación de esquema); `last_inbound_at` en `add_inbound`.
3. `create_message` y `publish_notice` devuelven el hilo; `save_notice`/`find_notice`.
4. `consultar_a_marco`, las guías nuevas, `questions_of`, `notice_text` con `RESPONDER` y la integración en `answer_batch`.
5. `channel_enabled` a `services`, `add_marco_reply`, `whatsapp_relay.py`, y el router de Google Chat (E7, E8).
6. README, con el `UPDATE` del prompt.
7. Versión 3.7.0 y `uv sync`.

## Release
1. La migración la aplica `entrypoint.sh` al desplegar; también se puede aplicar antes en la BD remota, porque 3.6.0 no lee la tabla ni la columna.
2. Merge a `main`: `release.yaml` despliega en el namespace `virtual-assistant`.
3. Aplicar el `UPDATE` del prompt del README, revisado por Marco: quitar las frases literales y cambiar «sin información → derivar» por «sin información → consultar».
4. Verificación manual de la spec desde otro teléfono.

## Risks
- **Google Chat no permite responder en hilo dentro del DM con la app:** todo el diseño cae. Se comprueba primero (paso 1). La alternativa es el space aparte que se descartó en la spec.
- **La redacción cambia el sentido de lo que escribió Marco:** la instrucción fija prohíbe agregar o quitar datos, y el hilo muestra el texto enviado (RF-14), así que Marco puede corregir con otro mensaje.
- **Un turno del asistente en curso cruza la respuesta de Marco:** el contacto puede recibir ambas, porque no se sube `epoch` (D6). Es poco probable, porque Marco suele responder minutos después.
- **El asistente consulta de más:** se calibra con el prompt y la docstring, sin redeploy.
- **Avisos anteriores a la 3.7.0:** sus hilos no tienen fila y van al «Agente Interno». Queda dicho en el README.
- **Latencia en el hilo:** la redacción y el envío pueden tardar 25 s. Si Google Chat corta antes, el contacto puede recibir el mensaje sin que el hilo lo confirme. Se mitiga con el límite de 15 s y el timeout de 10 s del envío.
