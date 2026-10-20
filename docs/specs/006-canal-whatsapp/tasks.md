# Tareas 006 — Canal WhatsApp con YCloud

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento incluye sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). La versión solo sube en T14.

## Grafo

```
T01 ─┬─► T02 ───────────┐
     ├─► T03 ─┬─► T04 ──┼──────────────┐
     │        └─────────┴─► T06 ─► T07 ─┼─► T08 ─┬─► T09 ─┬─► T11 ─► T12 ─► T13 ─► T14 ─► T15
     └─► T05 ───────────────────────────┘        └─► T10 ─┘
```

## Fase 0 — Gate

- [x] **T01 — Cerrar las preguntas abiertas del plan**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** obtener del usuario: (1) la aprobación del esquema (tres tablas de `src/models/whatsapp.py`, regla de
    `AGENTS.md`); (2) la confirmación de D9 (`503` si la BD falla en el webhook), D12 (los lotes fallidos no se
    reencolan) y D13 (recuperar lotes de menos de 30 min). Si alguna cambia, actualizar `plan.md` antes de seguir.
    Pasar el `Status` de `spec.md` y `plan.md` a `Approved` y hacer commit de la carpeta de la spec en
    `feature/whatsapp`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved`, D9, D12 y D13 coinciden con lo que confirmó
    el usuario y `git log` muestra el commit de la spec, el plan y las tareas.

## Fase 1 — Cimientos

- [ ] **T02 — Modelos y migración del canal**
  - **RF:** RF-04, RF-07, RF-22, RF-25
  - **Depende de:** T01 (puede ir en paralelo con T03 y T05)
  - **Alcance:** `src/models/whatsapp.py` con `WhatsappConversation`, `WhatsappMessage` y `WhatsappEvent` tal como
    están en **Data model**, con los `CheckConstraint` de `direction` y `status` agregados a mano y el índice de
    `received_at`. Migración con `uv run alembic revision --autogenerate -m "whatsapp channel"`, revisada a mano:
    `downgrade` borra `whatsapp_messages` antes que las otras dos tablas.
  - **Hecho cuando:** `uv run alembic upgrade head` y luego `uv run alembic downgrade -1 && uv run alembic upgrade head`
    terminan sin error, `uv run alembic check` no reporta diferencias y `uv run pyright` da 0 errores.

- [ ] **T03 — Excepción y esquemas del webhook**
  - **RF:** RF-05, RF-20
  - **Depende de:** T01 (puede ir en paralelo con T02 y T05)
  - **Alcance:** `InvalidSignatureError` en `src/utils/exceptions/whatsapp.py`. `src/interfaces/whatsapp.py` con
    `WhatsappEvent`, `InboundMessage`, `WhatsappMessage` y `Media`: alias camelCase como `ChatModel`, `from` como
    `from_`, campos extra ignorados y todos los tipos de contenido opcionales.
  - **Hecho cuando:** `uv run pyright` da 0 errores y los payloads de ejemplo de YCloud (entrante de texto, imagen,
    audio, sticker, reacción, echo y `whatsapp.message.updated` con `failed`), que T07 usará como fixtures en
    `tests/whatsapp_reply_test.py`, se parsean con `WhatsappEvent.model_validate`.

- [ ] **T04 — Cliente de YCloud**
  - **RF:** RF-02, RF-12, RF-13, RF-14, RF-17
  - **Depende de:** T03
  - **Alcance:** `src/services/ycloud.py`: `verify_signature` con el cuerpo crudo, `hmac.compare_digest` y 300 s de
    tolerancia (D8); `send_text` a `sendDirectly`; `mark_as_read`; `download_media` en streaming con `X-API-Key` y
    corte en `MAX_BYTES`; `split_text` por párrafos y espacios con límite 4096 (D15). Recibe el `httpx.AsyncClient`
    como parámetro, como `src/services/google_chat.py`.
  - **Hecho cuando:** U-Y1 a U-Y9 pasan con `uv run pytest tests/ycloud_test.py`.

- [ ] **T05 — Historial previo en `ask` y transcripción de audio**
  - **RF:** RF-18, RF-26
  - **Depende de:** T01 (puede ir en paralelo con T02 y T03)
  - **Alcance:** parámetro `previous: Sequence[BaseMessage] = ()` en `ask` de `src/services/assistant.py`, que antepone
    esos mensajes al `HumanMessage` (D4). `transcribe_audio(data, mime, llm)` y la constante `TRANSCRIBE_AUDIO` en
    `src/services/attachments.py`, sin tocar `TRANSCRIBED` (D10).
  - **Hecho cuando:** U-H1 y U-A1 pasan, y `tests/chat_test.py`, `tests/google_chat_test.py`,
    `tests/google_chat_reply_test.py` y `tests/attachments_test.py` siguen pasando sin cambios en sus casos.

## Fase 2 — Servicio del canal

- [ ] **T06 — Acceso a datos del canal**
  - **RF:** RF-04, RF-07, RF-08, RF-11, RF-22, RF-23, RF-28, RF-29, RF-30, RF-31
  - **Depende de:** T02, T03
  - **Alcance:** `src/services/whatsapp.py`: `register_event` (`ON CONFLICT DO NOTHING` y poda de más de 7 días),
    `read_settings` (12 h, 8 s y 120 s por defecto, sin caché), `add_inbound`, `add_echo` (pausa con
    `max(actual, send_time + ventana)`, `epoch + 1` y lote pendiente a `history`, D6), `claim_batch` con
    `SELECT … FOR UPDATE` y `processing_since` que se libera tras el tiempo máximo más 60 s (D3), `current_epoch`,
    `finish_batch`, `defer_batch` y `pending_phones` (D13). Errores de SQLAlchemy → `DatabaseUnavailableError`, como
    `src/services/agents.py`.
  - **Hecho cuando:** U-W22 y U-W23 pasan, `uv run pyright` da 0 errores y el SQL de `claim_batch` y `register_event`
    queda señalado para revisarlo en el PR (los tests no ejecutan SQL).

- [ ] **T07 — Recepción y despacho de eventos**
  - **RF:** RF-04, RF-05, RF-20, RF-22, RF-23, RF-24, RF-25, RF-27, RF-35
  - **Depende de:** T06
  - **Alcance:** en `src/services/whatsapp_reply.py`: `handle_event` (duplicado → nada; entrante con `group_id` →
    ignorado; entrante → `add_inbound` y `schedule` si no está pausada; echo → `add_echo`; estado `failed` → log con
    `error_code` y `error_message`; otro tipo → ignorado), `describe` (línea del historial y si genera respuesta),
    `thread_key` (D7) y `schedule` con un `asyncio.Task` por teléfono registrado en `pending`. Fake `FakeStore` en
    `tests/whatsapp_reply_test.py`.
  - **Hecho cuando:** U-W1, U-W2, U-W16, U-W17, U-W18, U-W19, U-W21 y U-W27 pasan.

- [ ] **T08 — Procesamiento de lotes de texto**
  - **RF:** RF-07, RF-08, RF-09, RF-10, RF-11, RF-12, RF-13, RF-14, RF-15, RF-16, RF-25, RF-26, RF-32, RF-33, RF-34
  - **Depende de:** T04, T05, T07
  - **Alcance:** `process_conversation` (bucle `claim_batch` → espera hasta `batch_due_at` → lote, D2) y `answer_batch`:
    `ask(…, COORDINADOR = "Agente WhatsApp", AUDIENCIA = "externo", previous = historial)` dentro de
    `asyncio.timeout(máximo)`, comparación de `epoch` antes de enviar (D6), envío de las partes de `split_text` sin
    agregar texto (RF-15), `mark_as_read` del último entrante, `RemoveMessage` de la respuesta si se descarta o falla
    el envío (D5), y errores con log sin contenido y silencio (D12).
  - **Hecho cuando:** U-W3, U-W4, U-W5, U-W6, U-W7, U-W9, U-W10, U-W20, U-W24, U-W25, U-W26 y U-H2 pasan.

- [ ] **T09 — Medios y mensajes no soportados**
  - **RF:** RF-17, RF-18, RF-19, RF-20, RF-21
  - **Depende de:** T08 (puede ir en paralelo con T10)
  - **Alcance:** en `answer_batch`, imágenes y documentos con `read_attachments` y `download_media` (la leyenda va
    junto al bloque); notas de voz con `transcribe_audio`; fallos y archivos de más de 20 MB con su línea, sin cortar
    el lote; lotes de solo videos, stickers, ubicaciones, contactos o reacciones sin `ask` ni envío, con las filas en
    `history`. Los medios de mensajes en pausa no se leen (D11).
  - **Hecho cuando:** U-W11, U-W12, U-W13, U-W14 y U-W15 pasan.

- [ ] **T10 — Recuperación al arrancar y cancelación al apagar**
  - **RF:** RF-11
  - **Depende de:** T08 (puede ir en paralelo con T09)
  - **Alcance:** `recover(graph)`: agenda los teléfonos de `pending_phones` con último mensaje de menos de 30 min y pasa
    los más viejos a `history` con `defer_batch` (D13). `cancel_pending()` del canal, como el de
    `google_chat_reply`.
  - **Hecho cuando:** U-W8 pasa.

## Fase 3 — Integración

- [ ] **T11 — Endpoint y wiring en `main.py`**
  - **RF:** RF-01, RF-02, RF-03, RF-06, RF-28
  - **Depende de:** T09, T10
  - **Alcance:** `src/routers/whatsapp.py` con `POST /api/v1/whatsapp/events`: cuerpo crudo, `ycloud_webhook_secret`
    (falta → `503`), `verify_signature` (`401`), `whatsapp_enabled` (`200` sin procesar si no es `true`),
    `handle_event` y `200`; BD caída → `503` (D9). En `main.py`: `include_router`, `await recover(app.state.graph)` en
    el `lifespan` tras compilar el grafo y `cancel_pending()` del canal al apagar.
  - **Hecho cuando:** E1 a E5 pasan con `uv run pytest tests/whatsapp_test.py` y `/docs` lista la ruta nueva al
    correr `uv run fastapi dev`.

## Fase 4 — Documentación, calidad y release

- [ ] **T12 — README**
  - **RF:** —
  - **Depende de:** T11
  - **Alcance:** fila del endpoint en "Endpoints"; sección "WhatsApp (YCloud)" con las 6 properties y sus valores por
    defecto, el SQL para cargar el «Agente WhatsApp» (prompt en primera persona, sin comprometer fechas, precios ni
    reuniones), el registro del webhook en YCloud con los tres eventos, y el comportamiento de agrupación, pausa,
    tipos de mensaje y fallos silenciosos; las tres tablas en "Base de datos"; los archivos nuevos en "Estructura".
  - **Hecho cuando:** `README.md` contiene la ruta `/api/v1/whatsapp/events`, las properties `whatsapp_enabled`,
    `ycloud_api_key`, `ycloud_webhook_secret`, `whatsapp_pause_hours`, `whatsapp_grouping_seconds` y
    `whatsapp_timeout`, y las tablas `whatsapp_conversations`, `whatsapp_messages` y `whatsapp_events`.

- [ ] **T13 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T12
  - **Alcance:** checklist de `AGENTS.md` sobre toda la rama.
  - **Hecho cuando:** `uv run pytest` pasa completo (incluidos U-Y*, U-W*, U-H*, U-A1 y E1–E5), `uv run pyright` da
    0 errores, `uv run alembic check` no reporta diferencias y `uv run alembic upgrade head` no tiene migraciones
    pendientes en la BD local.

- [ ] **T14 — Versión 3.5.0**
  - **RF:** —
  - **Depende de:** T13
  - **Alcance:** `version = "3.5.0"` en `pyproject.toml` y `uv sync`, en un solo commit con `uv.lock`.
  - **Hecho cuando:** `git log -1` muestra `chore(release): Bump version to v3.5.0` con `pyproject.toml` y `uv.lock`.

- [ ] **T15 — Verificación manual tras el despliegue**
  - **RF:** RF-01, RF-12, RF-15, RF-18, RF-22, RF-24, RF-06
  - **Depende de:** T14 y el despliegue en el cluster
  - **Alcance:** seguir la sección **Release** del plan: cargar las properties y el «Agente WhatsApp» con el canal
    apagado, registrar el webhook en YCloud, confirmar los `200` en el log y activar `whatsapp_enabled`. Comprobar lo
    que los tests no cubren: el formato real de los echoes, la descarga de medios y la transcripción de una nota de
    voz `audio/ogg`.
  - **Hecho cuando:** desde otro teléfono, tres mensajes seguidos y una nota de voz reciben una sola respuesta en
    primera persona; las respuestas del asistente **no** pausan la conversación (riesgo de echo del plan); tras
    responder Marco desde su celular, el siguiente mensaje del contacto no recibe respuesta; dos mensajes casi
    simultáneos producen una sola respuesta; con `whatsapp_enabled = false` no se responde nada.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T11, T15 |
| RF-02 | T04, T11 |
| RF-03 | T11 |
| RF-04 | T02, T06, T07 |
| RF-05 | T03, T07 |
| RF-06 | T11, T15 |
| RF-07 | T02, T06, T08 |
| RF-08 | T06, T08 |
| RF-09 | T08 |
| RF-10 | T08 |
| RF-11 | T06, T08, T10 |
| RF-12 | T04, T08, T15 |
| RF-13 | T04, T08 |
| RF-14 | T04, T08 |
| RF-15 | T08, T15 |
| RF-16 | T08 |
| RF-17 | T04, T09 |
| RF-18 | T05, T09, T15 |
| RF-19 | T09 |
| RF-20 | T03, T07, T09 |
| RF-21 | T09 |
| RF-22 | T02, T06, T07, T15 |
| RF-23 | T06, T07 |
| RF-24 | T07, T15 |
| RF-25 | T02, T07, T08 |
| RF-26 | T05, T08 |
| RF-27 | T07 |
| RF-28 | T06, T11 |
| RF-29, RF-30, RF-31 | T06 |
| RF-32, RF-33, RF-34 | T08 |
| RF-35 | T07 |
