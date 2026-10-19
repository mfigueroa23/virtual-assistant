# virtual-assistant

Backend de asistente virtual, construido con FastAPI, SQLAlchemy (async), LangGraph, Gemini y PostgreSQL.

## Arquitectura

- Hay dos coordinadores: `Agente Externo` atiende a los clientes por `/chat` y `Agente Interno` a los colaboradores por Google Chat.
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

### Archivos adjuntos en Google Chat

- Se leen los archivos subidos al chat: JPG, PNG, WebP y PDF (los transcribe Gemini), Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), texto, Markdown, CSV y JSON. Los enlaces de Drive no se leen.
- Cada archivo se convierte a texto una vez y se suma al mensaje como `[Archivo adjunto: nombre]`, así queda en el historial y sirve para los mensajes siguientes.
- Un archivo de más de 20 MB no se descarga y el texto de más de 30.000 caracteres se lee en parte. Un archivo con problemas queda con una línea de estado y no impide leer los demás.
- Google Chat espera la respuesta en menos de 30 segundos: si leer los archivos y responder supera `google_chat_notice_seconds`, se responde un aviso y la respuesta se publica después como mensaje nuevo en el space (con mención al usuario si no es un mensaje directo). Si se supera `google_timeout`, el mensaje nuevo es el de demora.
- El aviso lo redacta el LLM con las instrucciones de `google_chat_notice_prompt`, a partir del texto del mensaje y los nombres de los adjuntos (sin leerlos ni usar el historial), y no se guarda en el historial. Si la respuesta queda lista mientras se redacta el aviso, se responde en el hilo y el aviso se descarta.
- `google_chat_notice_seconds` + `google_chat_notice_timeout_seconds` debe quedar por debajo de los 30 segundos de Google Chat; si no, Google puede descartar el aviso, aunque la respuesta se publica igual como mensaje nuevo.
- Ejemplo de `google_chat_notice_prompt`: «Redacta en español, en una o dos frases, un aviso amable que confirme qué estás revisando según el mensaje del usuario y diga que la respuesta llegará en un mensaje nuevo. No respondas la consulta.»

## Base de datos

| Tabla | Qué contiene |
|---|---|
| `property` | Configuración de la aplicación (`key` / `value`) |
| `agents` | Prompts de los agentes. Con `area_id` vacío es un coordinador, con `area_id` es el sub-agente de esa área |
| `areas` | Áreas con su descripción y audiencia (`externo` o `interno`) |
| `faq_categories` | Categorías de preguntas frecuentes de cada área |
| `faqs` | Preguntas frecuentes con su embedding. `status` es `re-index` si falta generar el embedding e `index` si ya está indexada |
| `cors_origins` | Orígenes que pueden consumir la API desde el navegador (CORS). `enabled` es `true` por defecto |

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
