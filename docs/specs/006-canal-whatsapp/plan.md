# Plan 006 — Canal WhatsApp con YCloud

- **Spec:** [spec.md](spec.md) (approved 2026-10-20)
- **Status:** Approved
- **Version:** 3.5.0 (feat → minor, desde 3.4.0)

El plan respeta la constitución: el router solo valida la firma y delega, la lógica y el SQL viven en `services` (§2),
los tests corren sin BD real, sin LLM y sin YCloud (§4), la configuración y los secretos van en `property` (§6) y el
contacto nunca recibe trazas ni mensajes de error (§7). No agrega dependencias: HTTP con `httpx`, la firma con
`hmac`/`hashlib` y las pruebas del historial con el `InMemorySaver` que ya trae `langgraph`. **Requiere aprobación de
esquema:** tres tablas nuevas en `src/models/whatsapp.py` con su migración (ver **Data model**), necesarias para que la
deduplicación, la pausa y los lotes sobrevivan a reinicios y funcionen con varias réplicas, como pide la spec. El
coordinador «Agente WhatsApp» y las properties nuevas son datos, sin cambio de esquema.

## Endpoints

| Método y ruta | Auth | Respuesta | RF |
|---|---|---|---|
| `POST /api/v1/whatsapp/events` (nuevo) | Cabecera `YCloud-Signature: t=<unix>,s=<hex>`, HMAC-SHA256 de `"{t}." + cuerpo crudo` con el secreto del endpoint | `200` sin cuerpo cuando el evento se aceptó, se ignoró o es duplicado. `401` si falta la firma, no coincide o tiene más de 5 min de diferencia. `503` si falta el secreto o la BD no está disponible (YCloud reintenta, D9) | RF-01 a RF-06, RF-22, RF-23, RF-35 |

## Modules

### New: `src/routers/whatsapp.py` (`router`)
| File | Responsibility | RF |
|---|---|---|
| `src/routers/whatsapp.py` | `POST /api/v1/whatsapp/events`. Lee el cuerpo crudo con `await request.body()` antes de parsear (D8). Obtiene `ycloud_webhook_secret` con `get_property` (si falta: `503` y log). Llama a `verify_signature`; con `InvalidSignatureError` responde `401`. Parsea con `WhatsappEvent.model_validate_json`. Si `whatsapp_enabled` no es `true`, responde `200` sin más (RF-06). Delega en `handle_event(graph, session, event)` y responde `200`. `DatabaseUnavailableError` o `psycopg.OperationalError` → `503` (D9). | RF-01 a RF-03, RF-06 |

### New: `src/interfaces/whatsapp.py`
| File | Responsibility | RF |
|---|---|---|
| `src/interfaces/whatsapp.py` | Modelos Pydantic con alias camelCase (como `ChatModel` de `src/interfaces/google_chat.py`), solo con los campos que se usan: `WhatsappEvent` (`id`, `type`, `whatsapp_inbound_message`, `whatsapp_message`), `InboundMessage` (`id`, `from_` alias `from`, `to`, `group_id`, `send_time`, `type`, `text`, `image`, `audio`, `document`, `video`, `sticker`, `location`, `contacts`, `reaction`), `WhatsappMessage` (echo y estado: `id`, `wamid`, `from_`, `to`, `type`, `text`, `status`, `error_code`, `error_message`, `send_time`), `Media` (`id`, `link`, `caption`, `filename`, `mime_type`). Campos extra ignorados. | RF-05, RF-17, RF-18, RF-20 |

### New: `src/services/ycloud.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/ycloud.py` | Cliente de YCloud sin estado, con `httpx.AsyncClient` recibido como parámetro (patrón de `src/services/google_chat.py`). `verify_signature(header, body, secret, now)`: parsea `t` y `s`, rechaza si `abs(now - t) > 300`, compara con `hmac.compare_digest` (D8). `send_text(api_key, from_, to, body, http)` → `POST https://api.ycloud.com/v2/whatsapp/messages/sendDirectly` con `X-API-Key`. `mark_as_read(api_key, inbound_id, http)` → `POST /v2/whatsapp/inboundMessages/{id}/markAsRead`. `download_media(api_key, link, http)`: descarga en streaming con `X-API-Key` y corta al pasar `MAX_BYTES` con `AttachmentTooLargeError`, como `download` de Google Chat. `split_text(text, limit = 4096)`: corta por párrafo, luego por espacio, nunca dentro de una palabra salvo que la palabra sola supere el límite (D15). | RF-02, RF-12 a RF-14, RF-17 a RF-19 |

### New: `src/services/whatsapp.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/whatsapp.py` | Acceso a datos del canal, todo con `AsyncSession`. `register_event(session, event_id) -> bool`: `INSERT … ON CONFLICT DO NOTHING` en `whatsapp_events` y borra los de más de 7 días; `False` si ya existía. `read_settings(session) -> WhatsappSettings`: pausa (12 h), agrupación (8 s), tiempo máximo (120 s) y API key, sin caché. `add_inbound(session, message, line, now, wait)`: crea o actualiza la conversación (`business_phone` desde `to`) y guarda el mensaje como `pending` con `batch_due_at = now + wait`, o como `history` si la conversación está pausada. `add_echo(session, message, line, window)`: guarda el mensaje de Marco como `history`, fija `paused_until = max(actual, send_time + window)`, incrementa `epoch` y pasa a `history` los mensajes `pending` del contacto (D6). `claim_batch(session, phone, now, stale)`: `SELECT … FOR UPDATE` de la conversación; devuelve `Wait(segundos)`, `Busy`, `Nothing` o `Batch(epoch, mensajes, historial, business_phone)` y marca el lote como `processing` y `processing_since = now` (D3). `current_epoch`, `finish_batch` (borra los mensajes ya volcados al historial y libera `processing_since`), `defer_batch` (pasa el lote a `history`) y `pending_phones(session, since)` para la recuperación (D13). | RF-04, RF-07, RF-08, RF-11, RF-22 a RF-29 |

### New: `src/services/whatsapp_reply.py`
| File | Responsibility | RF |
|---|---|---|
| `src/services/whatsapp_reply.py` | Orquestación. Constantes `COORDINADOR = "Agente WhatsApp"`, `AUDIENCIA = "externo"` y `thread_key(phone) = f"whatsapp:{phone}"` (D7). `handle_event(graph, session, event)`: si `register_event` dice duplicado, termina (RF-04). Despacha por `type`: entrante (ignora si trae `group_id`), echo (`add_echo`) o estado (`failed` → `logger.warning` con `error_code` y `error_message`, RF-35). Cualquier otro tipo se ignora (RF-05). Tras un entrante no pausado llama a `schedule(graph, phone)`. `describe(message) -> tuple[str, bool]`: línea del historial y si genera respuesta (RF-20). `schedule`: un `asyncio.Task` por teléfono en este proceso (`scheduled: dict[str, Task]`), registrado en `pending` como en `google_chat_reply`. `process_conversation(graph, phone)`: bucle `claim_batch` → espera → lote (D2). `answer_batch`: arma el texto del lote (`read_attachments` para imágenes y documentos, `transcribe_audio` para audios), llama a `ask(…, previous = historial)` dentro de `asyncio.timeout(máximo)`, compara `epoch` antes de enviar, envía las partes de `split_text`, marca como leído el último entrante y, si descarta o falla el envío, quita la respuesta del historial con `RemoveMessage` (D5). Errores: log sin contenido y silencio (RF-32 a RF-34). `recover(graph)` y `cancel_pending()` para el `lifespan`. | RF-07 a RF-21, RF-24, RF-25, RF-27, RF-30 a RF-34 |

### New: `src/utils/exceptions/whatsapp.py`
| File | Responsibility | RF |
|---|---|---|
| `src/utils/exceptions/whatsapp.py` | `InvalidSignatureError`: el evento no trae una firma válida de YCloud. | RF-02 |

### Changed
| File | Change | RF |
|---|---|---|
| `src/services/assistant.py` | `ask` recibe `previous: Sequence[BaseMessage] = ()` y los antepone al `HumanMessage` del turno: `{"messages": [*previous, HumanMessage(mensaje)]}`. `/chat` y Google Chat no lo pasan y no cambian (D4). | RF-26 |
| `src/services/attachments.py` | Nueva `transcribe_audio(data, mime, llm) -> str` con la instrucción fija `TRANSCRIBE_AUDIO` y un bloque `{"type": "audio", "base64": …, "mime_type": …}`, igual que `to_text`. `TRANSCRIBED` no cambia, así que Google Chat sigue sin aceptar audios (D10). | RF-18 |
| `main.py` | `include_router(whatsapp_router)`. En el `lifespan`, después de compilar el grafo: `await recover(app.state.graph)`. Al apagar, `await cancel_pending()` del canal WhatsApp, junto al de Google Chat. | RF-11 |

### Infrastructure
| File | Change |
|---|---|
| `src/models/whatsapp.py` + `alembic/versions/<rev>_whatsapp_channel.py` | Tres tablas nuevas (ver **Data model**). Migración generada con `uv run alembic revision --autogenerate -m "whatsapp channel"` y revisada a mano. **Requiere aprobación.** |
| `tests/ycloud_test.py` | Firma, división del texto y llamadas HTTP con `httpx.MockTransport` (U-Y*). |
| `tests/whatsapp_reply_test.py` | Orquestación con las funciones de `src/services/whatsapp.py` reemplazadas por un fake en memoria (`FakeStore`) con `monkeypatch`, un `ask` falso y YCloud falso (U-W*). Historial real con `coordinator_builder.compile(checkpointer = InMemorySaver())` para `previous` y `RemoveMessage` (U-H*). |
| `tests/whatsapp_test.py` | Endpoint con `TestClient` y `app.dependency_overrides`, como `tests/google_chat_test.py` (E*). |
| `tests/attachments_test.py` | Caso de `transcribe_audio` con un modelo falso. |
| `README.md` | Endpoint en la tabla, sección "WhatsApp (YCloud)": properties, carga del «Agente WhatsApp», registro del webhook en YCloud, comportamiento de pausa y agrupación. Tablas nuevas en "Base de datos". |
| `pyproject.toml` / `uv.lock` | 3.4.0 → 3.5.0 y `uv sync` en `chore(release): Bump version to v3.5.0` al cerrar todas las tareas. |

## Data model

```python
class WhatsappConversation(Base):
  __tablename__ = "whatsapp_conversations"

  # Número del contacto en E.164, tal como lo envía YCloud.
  phone: Mapped[str] = mapped_column(String(32), primary_key=True)
  # Número del negocio al que escribió el contacto: es el "from" de las respuestas.
  business_phone: Mapped[str] = mapped_column(String(32))
  # Hasta cuándo el asistente no responde porque Marco tomó la conversación (RF-22, RF-23).
  paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
  # Cuándo vence la espera de agrupación del lote en curso (RF-07, RF-08).
  batch_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
  # Quién procesa un lote: evita dos respuestas a la vez y se libera sola si queda vieja (D3).
  processing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone = True))
  # Sube con cada mensaje de Marco: si cambió durante el lote, la respuesta se descarta (RF-25, D6).
  epoch: Mapped[int] = mapped_column(Integer, server_default = "0")

class WhatsappMessage(Base):
  __tablename__ = "whatsapp_messages"

  # Id de YCloud del mensaje (entrante o echo).
  id: Mapped[str] = mapped_column(String(64), primary_key = True)
  phone: Mapped[str] = mapped_column(ForeignKey("whatsapp_conversations.phone", ondelete = "CASCADE"), index = True)
  # inbound (contacto) o echo (Marco desde la app).
  direction: Mapped[str] = mapped_column(String(10))
  # pending → processing → se borra al volcarse al historial; history espera el próximo lote (D4).
  status: Mapped[str] = mapped_column(String(20))
  sent_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
  type: Mapped[str] = mapped_column(String(20))
  # Texto, leyenda o línea descriptiva (RF-20).
  text: Mapped[str | None] = mapped_column(Text)
  media_link: Mapped[str | None] = mapped_column(Text)
  media_mime: Mapped[str | None] = mapped_column(String(255))
  media_name: Mapped[str | None] = mapped_column(String(255))

class WhatsappEvent(Base):
  __tablename__ = "whatsapp_events"

  # Id del evento de YCloud, para descartar reintentos (RF-04).
  id: Mapped[str] = mapped_column(String(64), primary_key = True)
  received_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now(), index = True)
```

- Una conversación tiene muchos mensajes. Los mensajes son una **cola**, no un archivo: se borran al quedar en el
  historial del checkpointer, así que la tabla solo guarda lo pendiente (privacidad, restricción de la spec).
- `CheckConstraint` a mano, como en `areas`: `direction IN ('inbound', 'echo')` y
  `status IN ('pending', 'processing', 'history')`.
- `whatsapp_events` se poda en cada inserción (más de 7 días); YCloud deja de reintentar a las ~6 h.
- La migración solo crea tablas. `downgrade` las borra en orden inverso (`whatsapp_messages`, luego las otras dos).
- Properties nuevas (filas de `property`): `whatsapp_enabled` (`true` para activar; si falta, apagado),
  `ycloud_api_key` y `ycloud_webhook_secret` (secretos), `whatsapp_pause_hours` (12), `whatsapp_grouping_seconds` (8)
  y `whatsapp_timeout` (120).
- Fila nueva en `agents`: `name = 'Agente WhatsApp'`, `area_id = NULL`, prompt en primera persona. Se carga con el SQL
  del README.

## Decisions
| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Lotes, pausa y deduplicación en tablas de PostgreSQL | Diccionarios y temporizadores en memoria del proceso | La spec exige sobrevivir a reinicios y funcionar con varias réplicas: en memoria, un deploy perdería los lotes en espera y dos réplicas responderían dos veces. |
| D2 | La espera de agrupación es una columna `batch_due_at` que cada mensaje empuja, y una tarea por teléfono duerme hasta esa hora y vuelve a mirar | Un sondeo global cada N segundos que busque lotes vencidos | Con la columna, RF-08 (reiniciar la espera) es una sola escritura y no hay consultas en vacío. La tarea solo existe mientras hay un lote abierto. |
| D3 | Reclamar el lote con `SELECT … FOR UPDATE` y marcar `processing_since`, que se considera libre al pasar el tiempo máximo más 60 s | Un advisory lock de PostgreSQL retenido durante todo el `ask` | El lock retendría una conexión del pool hasta 120 s por conversación. Con la marca, la transacción dura milisegundos y un proceso caído no bloquea la conversación para siempre (RF-11). |
| D4 | Los mensajes de Marco y los del contacto durante la pausa se guardan como filas `history` y se vuelcan al historial en el siguiente `ask`, como `previous` | Escribirlos al instante con `graph.aupdate_state` | `aupdate_state` concurrente con un `ainvoke` del mismo hilo crea checkpoints paralelos y uno se pierde. Así, solo el lote reclamado escribe en el hilo. Cumple RF-26: el asistente ve ese contexto antes de su próxima respuesta. |
| D5 | Si la respuesta se descarta (echo durante el lote) o YCloud la rechaza, se borra del historial con `RemoveMessage` | Dejarla en el historial | El historial debe reflejar lo que el contacto recibió. Si no, el asistente creería haber dicho algo que nunca llegó (RF-25, RF-34). |
| D6 | Un contador `epoch` que el echo incrementa y que se compara justo antes de enviar | Comparar `paused_until` antes y después | El contador detecta cualquier mensaje de Marco aunque no cambie la hora de fin de la pausa (por ejemplo, un echo con `sendTime` anterior). Es una comparación de enteros. |
| D7 | Hilo del checkpointer `whatsapp:{phone}` | El número tal cual | `/chat` usa UUID y Google Chat usa `spaces/…`. El prefijo garantiza la separación de RF-10 sin depender del formato de cada canal. |
| D8 | Verificar la firma sobre el cuerpo crudo antes de parsear, con `hmac.compare_digest` y 300 s de tolerancia | Parsear con Pydantic y volver a serializar para firmar | Volver a serializar cambia espacios y orden de claves y la firma no coincide, como advierte YCloud. Sin dependencias nuevas (constitución §1). |
| D9 | Si falta el secreto o la BD falla en el webhook, se responde `503` | Responder `200` y perder el evento | YCloud reintenta 7 veces en ~6 h ante un no-2xx, así que un corte breve de la BD no pierde mensajes. La spec fija el `503` del secreto (RF-03); la BD sigue el mismo criterio. |
| D10 | `transcribe_audio` nueva en `attachments.py`, sin agregar audio a `TRANSCRIBED` | Agregar `audio/ogg` y similares a `TRANSCRIBED` | Agregarlos cambiaría lo que acepta Google Chat, que está fuera de alcance. La función reutiliza el mismo patrón de bloque base64 que las imágenes. |
| D11 | Los medios recibidos durante la pausa no se leen: quedan como una línea con su tipo y leyenda | Leerlos también | El asistente no responde esos mensajes y solo necesita el contexto. Leerlos costaría llamadas al LLM por cada archivo que Marco ya está atendiendo. |
| D12 | Si el lote falla (RF-32, RF-33), sus mensajes se borran de la cola sin reencolarlos | Pasarlos a `history` para el próximo lote | Según dónde falle, `ask` ya puede haber guardado el turno en el checkpointer, y reencolarlo lo duplicaría. El mensaje sigue en el celular de Marco, que es quien responde cuando el asistente calla. |
| D13 | Al arrancar se retoman los lotes cuyo último mensaje tiene menos de 30 min; los más viejos pasan a `history` | Retomarlos todos, o descartarlos todos | Un deploy dura minutos y no debe dejar mensajes sin respuesta. Responder horas tarde, en cambio, choca con que Marco probablemente ya contestó. 30 min es una constante documentada. |
| D14 | El número desde el que se responde sale del `to` del mensaje entrante y se guarda en la conversación | Una property con el número del negocio | Un dato menos que configurar, y siempre es el número al que escribió el contacto. |
| D15 | Dividir la respuesta por párrafos y luego por espacios, con límite 4096 | Cortar cada 4096 caracteres | RF-13 pide no cortar palabras. 4096 es el máximo de `text.body` en `sendDirectly`. |

## RF coverage
| RF | Where | Tests |
|---|---|---|
| RF-01 | `routers/whatsapp.py` (responde antes de procesar; la tarea va en segundo plano) | E1 |
| RF-02 | `ycloud.verify_signature`, router | U-Y1 a U-Y4, E2 |
| RF-03 | router | E3 |
| RF-04 | `whatsapp.register_event`, `handle_event` | U-W1, E4 |
| RF-05 | `handle_event` | U-W2 |
| RF-06 | router | E5 |
| RF-07, RF-08 | `add_inbound` (`batch_due_at`), `process_conversation` | U-W3, U-W4 |
| RF-09 | `answer_batch` | U-W5 |
| RF-10 | `thread_key` | U-W6 |
| RF-11 | `claim_batch` (`Busy`), `process_conversation`, `recover` | U-W7, U-W8 |
| RF-12 | `answer_batch`, `ycloud.send_text` | U-W5, U-Y5 |
| RF-13 | `ycloud.split_text` | U-Y6, U-Y7 |
| RF-14 | `answer_batch`, `ycloud.mark_as_read` | U-W9, U-Y8 |
| RF-15 | `answer_batch` (envía el texto de `ask` sin cambios) | U-W5 |
| RF-16 | `agents/coordinator.py` y `agents/nodes.py` sin cambios | U-W10 |
| RF-17 | `answer_batch` + `read_attachments` + `ycloud.download_media` | U-W11, U-Y9 |
| RF-18 | `attachments.transcribe_audio` | U-W12, U-A1 |
| RF-19 | `read_one` existente, `answer_batch` | U-W13 |
| RF-20, RF-21 | `describe`, `answer_batch` | U-W14, U-W15 |
| RF-22, RF-23 | `add_echo` | U-W16, U-W17 |
| RF-24 | `add_inbound` (pausada → `history`, sin `schedule`) | U-W18 |
| RF-25 | `add_echo` (pending → history), comparación de `epoch`, `RemoveMessage` | U-W19, U-W20, U-H2 |
| RF-26 | `ask(previous = …)` | U-H1 |
| RF-27 | `add_inbound` tras vencer `paused_until` | U-W21 |
| RF-28 a RF-31 | `read_settings`, router | U-W22, U-W23, E5 |
| RF-32 | `answer_batch` | U-W24 |
| RF-33 | `answer_batch` | U-W25 |
| RF-34 | `answer_batch` | U-W26, U-H2 |
| RF-35 | `handle_event` | U-W27 |

## Test strategy

**Unit — YCloud (`tests/ycloud_test.py`)**
- U-Y1 Firma válida: no lanza.
- U-Y2 Firma con un byte distinto en el cuerpo: `InvalidSignatureError`.
- U-Y3 Marca de tiempo a 301 s: `InvalidSignatureError`; a 299 s, válida.
- U-Y4 Cabecera ausente o mal formada (sin `t` o sin `s`): `InvalidSignatureError`.
- U-Y5 `send_text` envía `from`, `to`, `type: text`, `text.body` y `X-API-Key` (`MockTransport`); un `4xx` lanza `httpx.HTTPStatusError`.
- U-Y6 `split_text` de 9000 caracteres con párrafos: partes ≤ 4096, en orden y sin perder texto.
- U-Y7 `split_text` sin saltos de línea: corta en espacios, ninguna palabra partida.
- U-Y8 `mark_as_read` llama a `/inboundMessages/{id}/markAsRead` con `X-API-Key`.
- U-Y9 `download_media` corta al pasar `MAX_BYTES` con `AttachmentTooLargeError`.

**Unit — orquestación (`tests/whatsapp_reply_test.py`)** con `FakeStore` (fake en memoria de las funciones de `src/services/whatsapp.py`), `ask` falso y YCloud falso:
- U-W1 Evento repetido: `handle_event` no agenda ni guarda nada.
- U-W2 Tipo desconocido (`contact.created`): ignorado.
- U-W3 Tres entrantes seguidos: una sola llamada a `ask` con los tres textos en orden.
- U-W4 Un entrante que llega antes de vencer la espera la reinicia: `ask` no se llama hasta la nueva hora.
- U-W5 `ask` recibe `COORDINADOR = "Agente WhatsApp"` y `AUDIENCIA = "externo"`, y `send_text` recibe exactamente su texto.
- U-W6 El hilo es `whatsapp:+56…`.
- U-W7 Con un lote en `processing`, un entrante nuevo se procesa en un segundo `ask` después del primero.
- U-W8 `recover` retoma un lote de hace 5 min y pasa a `history` uno de hace 2 h.
- U-W9 Tras enviar se marca como leído el último entrante del lote.
- U-W10 Las tools del coordinador son `[delegar_area]` y las del sub-agente `[search_faq]`.
- U-W11 Imagen con leyenda: el texto del lote incluye la leyenda y el bloque de `read_attachments`.
- U-W12 Nota de voz: el texto incluye la transcripción.
- U-W13 Archivo que falla al descargar: línea "no se pudo leer" y `ask` igual se llama.
- U-W14 Sticker con texto en el mismo lote: se responde el texto y el sticker queda como línea.
- U-W15 Lote con solo un sticker y una reacción: sin `ask` ni envío; las filas quedan en `history`.
- U-W16 Echo: `paused_until = send_time + 12 h`.
- U-W17 Segundo echo: renueva desde su `send_time`; uno más viejo no acorta la pausa.
- U-W18 Entrante con la conversación pausada: no agenda tarea y queda en `history`.
- U-W19 Echo con un lote en espera: el lote pasa a `history` y no hay `ask`.
- U-W20 Echo durante el `ask`: el `epoch` cambia y no se envía nada.
- U-W21 Entrante después de vencer la pausa: se responde solo ese mensaje, no los de la pausa.
- U-W22 Sin las properties de pausa, agrupación y tiempo máximo: 12 h, 8 s y 120 s.
- U-W23 Con valores configurados, se usan los de la BD (leídos en cada evento).
- U-W24 `ask` lanza `AgentNotFoundError` o falta `ycloud_api_key`: sin envío, log sin el texto del mensaje (`caplog`).
- U-W25 `ask` bloqueado más que el tiempo máximo: se cancela, sin envío, log de demora.
- U-W26 `send_text` falla: un solo intento y log.
- U-W27 Estado `failed`: log con `error_code` y `error_message`; `sent` o `delivered` no registran nada.

**Unit — historial (`tests/whatsapp_reply_test.py`)** con el grafo real compilado sobre `InMemorySaver` y un modelo falso:
- U-H1 `ask(previous = [HumanMessage, AIMessage])` deja en el hilo los mensajes previos antes del turno nuevo.
- U-H2 Tras `RemoveMessage` de la respuesta descartada, el hilo conserva el mensaje del contacto y no la respuesta.

**Unit — adjuntos (`tests/attachments_test.py`)**
- U-A1 `transcribe_audio` envía un bloque `audio` con el MIME recibido y devuelve el texto del modelo.

**E2E del endpoint (`tests/whatsapp_test.py`)** con `TestClient`, sesión anulada y `get_property`/`handle_event` reemplazados:
- E1 Firma válida y canal habilitado: `200` y `handle_event` llamado una vez; el endpoint no espera a la tarea.
- E2 Sin cabecera, firma alterada o vencida: `401` y `handle_event` sin llamar.
- E3 Sin `ycloud_webhook_secret`: `503`.
- E4 BD no disponible al registrar el evento: `503`.
- E5 `whatsapp_enabled` ausente o `false`: `200` y `handle_event` sin llamar.

**Limitaciones de los fakes.** El `FakeStore` no ejecuta SQL, así que no comprueba `FOR UPDATE`, `ON CONFLICT`, los
`CheckConstraint` ni el borrado en cascada. Se cubren con `uv run alembic check`, una revisión del SQL en el code review
y la prueba manual con dos mensajes casi simultáneos. Tampoco se prueba contra YCloud ni Gemini reales: la descarga de
medios, el formato real de los echoes y la transcripción de una nota de voz `audio/ogg` se verifican a mano.

## Implementation order
1. Modelos `src/models/whatsapp.py`, migración y `alembic check` (con la aprobación de esquema).
2. `src/utils/exceptions/whatsapp.py` y `src/interfaces/whatsapp.py`.
3. `src/services/ycloud.py` con U-Y*.
4. `ask(previous = …)` y `transcribe_audio`, con U-H1 y U-A1.
5. `src/services/whatsapp.py` (acceso a datos).
6. `src/services/whatsapp_reply.py` con U-W* y U-H2.
7. `src/routers/whatsapp.py` y `main.py` (router, `recover`, `cancel_pending`), con E*.
8. README.
9. Versión 3.5.0 y `uv sync` (commit de release).

## Release
1. Merge a `main`: el workflow despliega la imagen y `entrypoint.sh` aplica la migración al arrancar.
2. Cargar los datos con el canal todavía apagado: las properties `ycloud_api_key`, `ycloud_webhook_secret`,
   `whatsapp_pause_hours`, `whatsapp_grouping_seconds` y `whatsapp_timeout`, y la fila «Agente WhatsApp» con su prompt
   en primera persona (sin comprometer fechas, precios ni reuniones).
3. En YCloud, **Developers → Webhooks**: crear el endpoint `https://<host público>/api/v1/whatsapp/events` con
   `whatsapp.inbound_message.received`, `whatsapp.smb.message.echoes` y `whatsapp.message.updated`, y guardar el
   secreto en `ycloud_webhook_secret`.
4. Comprobar en el log que los eventos llegan con `200` (el canal sigue apagado).
5. `UPDATE property SET value = 'true' WHERE key = 'whatsapp_enabled'` (o insertarla) y hacer la prueba manual de la
   spec desde otro teléfono.
6. Para apagar en caso de problemas: `whatsapp_enabled = false`; el siguiente evento ya no se procesa.

## Risks
- **Que los mensajes enviados por la API también lleguen como echo:** el asistente se pausaría a sí mismo. La documentación dice que los echoes son solo los enviados desde la app, pero se verifica en el paso 5 del release. Si pasara, se ignorarían los echoes cuyo `wamid` coincida con uno enviado por el asistente.
- **Carrera entre la comparación de `epoch` y el envío:** si Marco escribe en esos milisegundos, el asistente puede responder igual. La ventana es mínima y se acepta.
- **Medios de YCloud:** el enlace con `X-API-Key` dura 30 días, de sobra para un lote de segundos. Si YCloud cambiara el formato, la lectura falla con la línea de RF-19 y el texto se responde igual.
- **Privacidad:** los mensajes personales quedan en el checkpointer, sin poda. La cola se vacía al procesar y el log nunca guarda contenido. La retención del historial queda para otra spec.
- **Ventana de 24 h de WhatsApp:** si un lote recuperado (D13) cae fuera de la ventana, YCloud rechaza el envío y aplica RF-34 (log, sin reintento).
- **Prompt en primera persona sin aviso de IA:** el asistente puede comprometer a Marco. Se mitiga con el prompt (paso 2 del release) y con la pausa al responder él.
