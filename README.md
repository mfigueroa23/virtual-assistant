# virtual-assistant

Backend de asistente virtual, construido con FastAPI, SQLAlchemy (async), LangGraph, Gemini y PostgreSQL.

## Arquitectura

- Hay tres coordinadores: `Agente Externo` atiende a los clientes por `/chat`, `Agente Interno` a los colaboradores por Google Chat y `Agente WhatsApp` a los contactos del WhatsApp de Marco (vía YCloud), en primera persona y con las áreas de la audiencia `externo`.
- Cada coordinador delega la consulta en el sub-agente del área que corresponde (un sub-agente por área, según la audiencia `externo` o `interno`).
- Cada sub-agente busca en las preguntas frecuentes de su área con similitud coseno (pgvector) antes de responder.
- Los prompts, áreas y sub-agentes se configuran en la base de datos. El historial de cada conversación se guarda en PostgreSQL con el checkpointer de LangGraph.

## Requisitos

- Python 3.14, fijado en `.python-version` (uv lo usa automáticamente y lo descarga si no está instalado)
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL

> Python 3.15 todavía no es compatible: algunas dependencias (`ormsgpack`, `uuid-utils`, `pyyaml`, `zstandard`) no publican wheels para esa versión y uv intenta compilarlas desde el código fuente (`ormsgpack` y `uuid-utils` requieren Rust).

## Configuración

La configuración se divide en dos lugares:

| Dónde | Qué contiene |
|---|---|
| `.env` | Solo la conexión a la base de datos (`DB_*`) y `LOG_LEVEL` |
| Tabla `property` | Todo el resto de la configuración de la aplicación (pares `key` / `value`) |

Crea el `.env` a partir del ejemplo y completa los valores:

```bash
cp .env.example .env
```

| Variable | Descripción | Por defecto |
|---|---|---|
| `DB_HOST` | Host de PostgreSQL | — |
| `DB_PORT` | Puerto de PostgreSQL | — |
| `DB_USER` | Usuario | — |
| `DB_PASSWORD` | Contraseña | — |
| `DB_NAME` | Nombre de la base de datos | — |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` (en mayúsculas) | `INFO` |

Las properties se leen de la base de datos en cada petición (sin caché), así que un cambio en la tabla se aplica desde la siguiente petición sin reiniciar la aplicación.

Properties usadas actualmente:

| Key | Uso |
|---|---|
| `service_name` | Nombre del servicio que devuelve `/health` |
| `google_api_key` | API key de Gemini |
| `google_model` | Modelo de chat de Gemini |
| `google_embedding_model` | Modelo de embeddings de las preguntas frecuentes |
| `google_chat_audience` | Audiencia (`aud`) esperada en el ID token del complemento de Google Chat |
| `google_chat_addon_service_account` | Correo de la cuenta de servicio del complemento de Google Chat |
| `google_chat_welcome` | Mensaje de bienvenida al agregar el asistente a un space |
| `google_chat_service_account_json` | JSON de la cuenta de servicio de la app de Chat (`client_email`, `private_key`, `token_uri`) para descargar los archivos adjuntos y publicar las respuestas diferidas con el scope `chat.bot`. Es un secreto. Si falta o no es válido, los archivos quedan como no leídos y se responde igual el texto, pero las respuestas diferidas no se pueden publicar |
| `google_chat_notice_seconds` | Umbral de aviso en segundos: si el asistente no responde antes, Google Chat recibe un aviso y la respuesta llega después como mensaje nuevo. Si falta se usa `10` |
| `google_chat_notice_prompt` | Instrucciones con las que el LLM redacta el aviso a partir del mensaje del usuario y los nombres de sus adjuntos. Si falta se responde el aviso fijo, sin llamar al LLM |
| `google_chat_notice_timeout_seconds` | Tiempo límite en segundos para que el LLM redacte el aviso, contado desde que vence el umbral. Si se supera, o el LLM falla, se responde el aviso fijo. Si falta se usa `5` |
| `google_timeout` | Tiempo máximo total en segundos para responder un mensaje de Google Chat; al superarlo se responde un mensaje de demora. Valor sugerido: `120` |
| `whatsapp_enabled` | `true` para que el asistente atienda WhatsApp. Si falta o tiene otro valor, los eventos se aceptan con `200` pero no se procesan |
| `ycloud_api_key` | API key de YCloud (cabecera `X-API-Key`) para enviar mensajes, marcarlos como leídos y descargar medios. Es un secreto |
| `ycloud_webhook_secret` | Secreto de firma del endpoint de webhook de YCloud, para verificar `YCloud-Signature`. Es un secreto. Si falta, el webhook responde `503` |
| `whatsapp_pause_hours` | Horas que el asistente deja de responder una conversación después de que Marco escribe desde el celular; cada mensaje suyo renueva la ventana. Si falta se usa `12` |
| `whatsapp_grouping_seconds` | Segundos sin mensajes nuevos del contacto que se esperan antes de responder la ráfaga completa. Si falta se usa `8` |
| `whatsapp_timeout` | Tiempo máximo en segundos para responder un lote de WhatsApp; al superarlo no se envía nada. Si falta se usa `120` |
| `whatsapp_notify_space` | Space de Google Chat donde llegan los avisos de derivación y de encargos de WhatsApp, con el formato `spaces/<id>` (ver [WhatsApp (YCloud)](#whatsapp-ycloud)). Publica con `google_chat_service_account_json`. Si falta, no hay aviso ni pausa por derivación y el asistente sigue respondiendo |
| `whatsapp_accepted_tasks` | Tipos de encargo que el asistente puede aceptar por WhatsApp, uno por línea. Si falta o está vacía, no acepta encargos y los deriva |
| `whatsapp_family_phones` | Números de la familia, uno por línea y en cualquier formato: se comparan solo los dígitos. Si falta o está vacía, nadie es familiar |
| `whatsapp_family_instructions` | Instrucciones de trato para la familia (tono, apodos, temas), que se suman al prompt solo en sus conversaciones |

> Los valores de la tabla `property` se guardan en texto plano. Revisa [SECURITY.md](SECURITY.md) antes de guardar secretos.

## Instalación

```bash
uv sync
uv run alembic upgrade head
```

Si `uv sync` falla compilando alguna dependencia, revisa que el entorno virtual use Python 3.14 (`uv run python --version`). Si se creó con otra versión (por ejemplo, después de que Homebrew actualizara `python3`), recréalo:

```bash
rm -rf .venv
uv sync
```

## Ejecución

```bash
uv run fastapi dev   # desarrollo, con recarga automática
uv run fastapi run   # producción
```

Documentación interactiva de la API: <http://127.0.0.1:8000/docs>

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Liveness: responde `true` si el proceso está vivo, sin consultar la base de datos |
| `GET` | `/health` | Verifica la base de datos. `200` si está disponible, `503` si no |
| `POST` | `/chat` | Chat web con el coordinador externo. Recibe `chat_id` (UUID generado por el cliente) y `mensaje` |
| `POST` | `/api/v1/google-chat/events` | Eventos del complemento de Google Chat con el coordinador interno. Exige el ID token del complemento en `Authorization: Bearer`, si no es válido responde `401` |
| `POST` | `/api/v1/whatsapp/events` | Webhook de YCloud para el WhatsApp de Marco con el coordinador de WhatsApp. Exige la cabecera `YCloud-Signature`: si falta, no coincide o tiene más de 5 minutos responde `401`; sin secreto o sin base de datos responde `503` para que YCloud reintente. En los demás casos responde `200` sin esperar la respuesta del asistente |

### Archivos adjuntos en Google Chat

- Se leen los archivos subidos al chat: JPG, PNG, WebP y PDF (los transcribe Gemini), Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), texto, Markdown, CSV y JSON. Los enlaces de Drive no se leen.
- Cada archivo se convierte a texto una vez y se suma al mensaje como `[Archivo adjunto: nombre]`, así queda en el historial y sirve para los mensajes siguientes.
- Un archivo de más de 20 MB no se descarga y el texto de más de 30.000 caracteres se lee en parte. Un archivo con problemas queda con una línea de estado y no impide leer los demás.
- Google Chat espera la respuesta en menos de 30 segundos: si leer los archivos y responder supera `google_chat_notice_seconds`, se responde un aviso y la respuesta se publica después como mensaje nuevo en el space (con mención al usuario si no es un mensaje directo). Si se supera `google_timeout`, el mensaje nuevo es el de demora.
- El aviso lo redacta el LLM con las instrucciones de `google_chat_notice_prompt`, a partir del texto del mensaje y los nombres de los adjuntos (sin leerlos ni usar el historial), y no se guarda en el historial. Si la respuesta queda lista mientras se redacta el aviso, se responde en el hilo y el aviso se descarta.
- `google_chat_notice_seconds` + `google_chat_notice_timeout_seconds` debe quedar por debajo de los 30 segundos de Google Chat; si no, Google puede descartar el aviso, aunque la respuesta se publica igual como mensaje nuevo.
- Ejemplo de `google_chat_notice_prompt`: «Redacta en español, en una o dos frases, un aviso amable que confirme qué estás revisando según el mensaje del usuario y diga que la respuesta llegará en un mensaje nuevo. No respondas la consulta.»

### WhatsApp (YCloud)

El número está conectado a YCloud en modo coexistencia: la app de WhatsApp Business del celular sigue funcionando y los mensajes llegan también por webhook. El asistente responde como Marco, sin presentarse como IA, todos los chats 1 a 1 (los grupos no llegan por la API).

- **Ráfagas:** cada mensaje del contacto reinicia una espera de `whatsapp_grouping_seconds`; al vencer, el asistente responde una sola vez a todo lo acumulado. Lo que llega mientras responde va en la respuesta siguiente.
- **Toma de la conversación:** cuando Marco escribe desde el celular (evento `whatsapp.smb.message.echoes`), el asistente deja de responder esa conversación durante `whatsapp_pause_hours`, contadas desde el último mensaje de Marco. Si estaba preparando una respuesta, la descarta. Los mensajes de la pausa, del contacto y de Marco, pasan al historial para que el asistente tenga el contexto al retomar.
- **Tipos de mensaje:** texto, imágenes y documentos (se leen como los adjuntos de Google Chat, hasta 20 MB) y notas de voz (las transcribe Gemini). Videos, stickers, ubicaciones, tarjetas de contacto y reacciones quedan en el historial como una línea, pero no generan respuesta por sí solos.
- **Respuestas:** se envían como texto, en varios mensajes si superan los 4096 caracteres, y los mensajes del contacto se marcan como leídos.
- **Derivación:** cuando el contacto quiere una reunión o una llamada, pregunta por precios, tiene una urgencia, insiste en hablar con Marco o pide un encargo que no se puede aceptar, el coordinador usa la tool `derivar_a_marco`. El contacto recibe la respuesta del turno, Marco recibe un aviso y la conversación queda en pausa `whatsapp_pause_hours`, contadas desde el aviso, igual que si Marco hubiera escrito.
- **Encargos:** lo que el contacto pide hacer, si es de un tipo de `whatsapp_accepted_tasks`, se registra con la tool `registrar_encargo`. El contacto recibe la confirmación, Marco recibe un aviso con qué hacer y quién lo pide, y el asistente sigue respondiendo, sin pausa.
- **Consultas:** lo que el asistente no sabe responder (una experiencia, una disponibilidad) lo pregunta a Marco con la tool `consultar_a_marco`. El contacto recibe la respuesta del turno y Marco un aviso con la pregunta; el asistente sigue atendiendo el resto de la conversación, sin pausa.
- **Respuestas del asistente al derivar, encargar o consultar:** las redacta con sus propias palabras. Las tools le dan el criterio (sin prometer plazos ni confirmar lo que Marco debe decidir) y nunca una frase para copiar.
- **Familia:** los números de `whatsapp_family_phones` reciben el trato de `whatsapp_family_instructions` y pueden pedir cualquier encargo, sin la lista de aceptables. Las reglas de seguridad del prompt y la derivación son las mismas para todos. El resto de los contactos recibe trato profesional.
- **Aviso:** un solo mensaje por turno en `whatsapp_notify_space`, con el nombre de perfil del contacto (o «Sin nombre»), «(familia)» si corresponde, su número y el enlace `https://wa.me/<número sin +>`. Si hubo derivación trae los motivos y un resumen de la conversación redactado por Gemini (o «No se pudo resumir la conversación.»); si hubo consultas, las preguntas («Te consulta:»); si hubo encargos, la lista. Siempre termina con «Responde en este hilo para contestarle por WhatsApp.». Si falla, se intenta 3 veces, con esperas de 1 y 2 segundos. Si no se publica, no hay pausa y queda en el log con el número del contacto, sin el contenido. Si Marco escribe mientras el asistente prepara la respuesta, no hay respuesta ni aviso. Si YCloud rechaza la respuesta, el aviso se publica igual.
- **Responder desde Google Chat:** lo que Marco escribe **en el hilo** de un aviso (con «Responder en hilo», no citando el mensaje) le llega al contacto por WhatsApp. Gemini lo redacta en el tono de la conversación (familiar o profesional) sin agregar ni quitar datos, y el hilo responde con el texto enviado. Cada mensaje en el hilo se envía por separado, queda en el historial como mensaje de Marco y levanta la pausa del chat, así que el asistente vuelve a atenderlo. Lo que Marco escribe fuera de los hilos de avisos sigue yendo al «Agente Interno».
- **Ventana de 24 h:** WhatsApp solo deja enviar texto libre por la API hasta 24 h después del último mensaje del contacto. Fuera de esa ventana no se envía nada y el hilo responde con el enlace `wa.me` para escribirle desde el celular. Tampoco se envía si el canal está apagado, si el mensaje no tiene texto o si falla la redacción o YCloud: el hilo lo dice y el log no guarda el texto. Los avisos publicados antes de la 3.7.0 no aceptan respuestas.
- **Fallos:** si el LLM, la base de datos o YCloud fallan, o se supera `whatsapp_timeout`, el contacto no recibe nada y queda en el log sin el contenido del mensaje. Marco responde desde su celular.
- **Conexiones salientes:** las llamadas a YCloud y a Google salen solo por IPv4, porque el cluster no tiene ruta IPv6, y reintentan la conexión hasta 2 veces si no logran conectar. Una petición que ya se envió no se repite, así que no se duplican mensajes.
- **Reinicios:** al arrancar se retoman los lotes con mensajes de los últimos 30 minutos; los más viejos quedan solo para el historial.

Para activar el canal:

1. Cargar el coordinador con su prompt en primera persona. Debe evitar comprometer a Marco con fechas, precios o reuniones; los criterios de derivación y de encargos van en el paso 5:

   ```sql
   INSERT INTO agents (name, prompt) VALUES ('Agente WhatsApp', 'Eres Marco Figueroa respondiendo tu WhatsApp...');
   ```

2. Cargar `ycloud_api_key` y, si se quieren otros valores, `whatsapp_pause_hours`, `whatsapp_grouping_seconds` y `whatsapp_timeout`.
3. En YCloud, **Developers → Webhooks**: crear un endpoint con la URL pública `https://<host>/api/v1/whatsapp/events` y los eventos `whatsapp.inbound_message.received`, `whatsapp.smb.message.echoes` y `whatsapp.message.updated`. Guardar el secreto que muestra YCloud (solo una vez) en `ycloud_webhook_secret`.
4. Revisar en el log que los eventos llegan con `200` y luego activar el canal:

   ```sql
   INSERT INTO property (key, value) VALUES ('whatsapp_enabled', 'true')
   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
   ```

5. Para la derivación, los encargos, las consultas y las respuestas desde el hilo, abrir en Google Chat el mensaje directo con la app y copiar el id de la URL (`https://chat.google.com/…/dm/<id>`). Cargar el space de aviso, los encargos aceptables y la familia:

   ```sql
   INSERT INTO property (key, value) VALUES
     ('whatsapp_notify_space', 'spaces/<id>'),
     ('whatsapp_accepted_tasks', E'Enviar mi CV o mi portafolio\nEnviar el enlace de un proyecto o de un artículo del blog'),
     ('whatsapp_family_phones', E'+56 9 1234 5678\n+56987654321'),
     ('whatsapp_family_instructions', 'Es tu familia: háblales con cariño, de tú y sin formalidades.')
   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
   ```

   Sumar los criterios al prompt del coordinador. Si el prompt dice que no aceptes encargos, quitar esa regla: ahora la deciden `whatsapp_accepted_tasks` y la familia.

   ```sql
   UPDATE agents SET prompt = prompt || E'\n\n'
     'Trato: con quien no sea de tu familia, responde de forma cordial y profesional.\n\n'
     'Derivar: usa derivar_a_marco cuando el contacto quiera agendar una reunión o una llamada, pregunte por precios o '
     'condiciones, tenga una urgencia, quiera hablar contigo en persona o pida un encargo que no puedes aceptar.\n\n'
     'Consultar: si te pregunta algo que no está en tu información, usa consultar_a_marco con la pregunta y sigue '
     'atendiendo el resto de la conversación.\n\n'
     'Encargos: si te pide hacer algo que puedes aceptar según tus instrucciones, usa registrar_encargo con lo que hay '
     'que hacer.\n\n'
     'En todos los casos responde con tus propias palabras, según lo que pidió y el tono de la conversación, sin '
     'prometer plazos ni repetir fórmulas.'
   WHERE name = 'Agente WhatsApp' AND area_id IS NULL;
   ```

6. Al pasar de la 3.6.0 a la 3.7.0, el prompt que ya está en la base trae frases de ejemplo que el modelo copia y deriva lo que no sabe. Este `UPDATE` cambia esas tres reglas; cada patrón debe coincidir una sola vez, así que conviene revisar el resultado con un `SELECT` antes:

   ```sql
   UPDATE agents SET prompt = regexp_replace(regexp_replace(regexp_replace(prompt,
     'Responde con calidez y brevedad, por ejemplo:[^\n]*',
     'Responde con calidez y brevedad, con tus propias palabras y sin repetir fórmulas.'),
     ' y di que lo revisas y respondes pronto, sin prometer plazos\.',
     ' y responde con tus propias palabras, sin prometer plazos.'),
     'Si no tienes información para una consulta profesional[^\n]*',
     'Si no tienes información para una consulta profesional, usa consultar_a_marco con la pregunta y sigue atendiendo '
     'el resto de la conversación. Si insiste en hablar contigo, usa derivar_a_marco.')
   WHERE name = 'Agente WhatsApp' AND area_id IS NULL;
   ```

Para apagarlo, `whatsapp_enabled = false`: aplica desde el siguiente evento.

## Base de datos

| Tabla | Qué contiene |
|---|---|
| `property` | Configuración de la aplicación (`key` / `value`) |
| `agents` | Prompts de los agentes. Con `area_id` vacío es un coordinador, con `area_id` es el sub-agente de esa área |
| `areas` | Áreas con su descripción y audiencia (`externo` o `interno`) |
| `faq_categories` | Categorías de preguntas frecuentes de cada área |
| `faqs` | Preguntas frecuentes con su embedding. `status` es `re-index` si falta generar el embedding e `index` si ya está indexada |
| `cors_origins` | Orígenes que pueden consumir la API desde el navegador (CORS). `enabled` es `true` por defecto |
| `whatsapp_conversations` | Una fila por contacto de WhatsApp: su nombre de perfil, cuándo escribió por última vez (para la ventana de 24 h), hasta cuándo está en pausa, cuándo vence la espera del lote, qué proceso lo responde y un contador (`epoch`) que sube con cada mensaje de Marco |
| `whatsapp_messages` | Cola de mensajes de WhatsApp pendientes de responder o de pasar al historial. Las filas se borran cuando quedan en el historial del checkpointer |
| `whatsapp_events` | Ids de los eventos de YCloud ya recibidos, para descartar reintentos. Se borran a los 7 días |
| `whatsapp_notices` | Hilo de Google Chat de cada aviso de WhatsApp y el contacto al que pertenece, para enviarle lo que Marco responde en ese hilo. Se borran junto con la conversación |

### Orígenes CORS

La API solo emite cabeceras CORS a los orígenes registrados y habilitados en `cors_origins`. La tabla se consulta en cada petición que trae la cabecera `Origin` (sin caché), así que un cambio aplica desde la siguiente petición sin reiniciar la aplicación. Las peticiones sin `Origin` (Google Chat, probes de Kubernetes) no la consultan.

El origen se compara de forma exacta con el que envía el navegador: `esquema://host[:puerto]`, en minúsculas, sin barra final ni ruta y sin el puerto por defecto (`https://app.ejemplo.com`, `http://localhost:5173`). No se admiten comodines ni subdominios: cada origen va en su propia fila.

```sql
-- Habilitar un origen
INSERT INTO cors_origins (origin) VALUES ('https://app.ejemplo.com');

-- Deshabilitarlo sin borrarlo
UPDATE cors_origins SET enabled = false WHERE origin = 'https://app.ejemplo.com';
```

Un origen no permitido recibe la respuesta sin cabeceras CORS (el navegador la bloquea) y su preflight se responde con `400`. Si la base de datos falla al consultar, el origen se trata como no permitido. No se envían credenciales (`Access-Control-Allow-Credentials`).

> No registres el origen `null`: lo envían iframes con sandbox y archivos locales (`file://`), y habilitarlo abriría la API a cualquiera de ellos.

Las tablas del checkpointer las crea LangGraph al arrancar la aplicación y no se versionan con alembic.

Para generar los embeddings de las preguntas frecuentes en estado `re-index`:

```bash
uv run python -m src.rag.reindex
```

## Migraciones

```bash
uv run alembic revision --autogenerate -m "descripción del cambio"
uv run alembic upgrade head
uv run alembic downgrade -1
```

Revisa siempre el archivo generado en `alembic/versions/` antes de aplicarlo.

## Tests

```bash
uv run pytest
```

Los tests nunca se conectan a la base de datos, a Google ni a Gemini: reemplazan la sesión con `app.dependency_overrides` o responden antes de consultarla. Igual necesitan las variables `DB_*` (del `.env` o del entorno) porque `Settings` las exige al importar la app.

## CI/CD

| Workflow | Cuándo | Qué hace |
|---|---|---|
| `.github/workflows/test.yaml` | Push a cualquier rama y PR a `main` | `pyright` + `pytest` |
| `.github/workflows/release.yaml` | Push a `main` | Verifica, publica la imagen en Docker Hub (`amd64` y `arm64`) y la despliega en Kubernetes (`deployment/chatbot-backend`, namespace `chatbot`) |

Configuración necesaria en GitHub:

| Tipo | Nombre | Uso |
|---|---|---|
| Variable | `DOCKERHUB_USERNAME` | Usuario de Docker Hub |
| Secret | `DOCKERHUB_TOKEN` | Token de Docker Hub |
| Secret | `LOCAL_NETWORK` | Service key de Twingate para llegar al cluster |
| Secret | `KUBE_CONFIG` | kubeconfig del cluster |

Las migraciones **no** se ejecutan en el pipeline: el contenedor las aplica al arrancar. `entrypoint.sh` ejecuta `alembic upgrade head` (no hace nada si la base de datos ya está en la última migración) y luego inicia la API. Si la migración falla, el contenedor no arranca. Con varias réplicas, un advisory lock de PostgreSQL en `alembic/env.py` hace que solo una las aplique a la vez.

## Estructura

```
main.py                     App FastAPI, lifespan y configuración de logs
entrypoint.sh               Entrypoint del contenedor: aplica migraciones y arranca la API
src/
├── config.py               Settings leídos del .env
├── agents/                 Coordinador, sub-agente, modelo de IA y tools (LangGraph)
├── database/session.py     Engine y sesión por request (SessionDep)
├── models/                 Modelos ORM de SQLAlchemy
├── interfaces/             Modelos Pydantic de request y respuesta
├── middlewares/            Middlewares ASGI (CORS con los orígenes de la base de datos)
├── rag/                    Re-indexación de los embeddings de preguntas frecuentes
├── routers/                Endpoints HTTP
├── services/               Lógica de negocio y acceso a datos
└── utils/exceptions/       Excepciones propias del proyecto
alembic/                    Migraciones de la base de datos
tests/                      Tests con pytest
```

## Autor

Marco Antonio Figueroa Sanchez

## Licencia

Distribuido bajo la licencia Apache 2.0. Ver [LICENSE](LICENSE).
