# Plan 013 — Remediación de los hallazgos de code scanning

- **Spec:** [spec.md](spec.md) (aprobada 2026-10-28)
- **Status:** Approved
- **Version:** 3.10.8 (fix → patch sobre la 3.10.7 de `main`; si Dependabot fusiona antes, el patch siguiente)

Respeta la constitución: no agrega dependencias de Python (§1), mantiene las capas (la validación vive en
`services/google_chat.py`, los routers no cambian, §2), no toca `src/models/` ni migraciones (§5), los errores nuevos
solo van al log y nunca llevan el valor rechazado (§7), y la imagen sigue arrancando sola con las migraciones y
`/health` (§10). La base `python:3.14-alpine` ya la aprobó el owner al planificar. Siguen pendientes de aprobación los
cambios de infra que pide la spec: `Dockerfile`, `release.yaml` y `sca.yaml` (`entrypoint.sh` no cambia, D5).

## Endpoints

Sin cambios. El webhook de Google Chat (`POST /google-chat/events`) responde igual. Lo que cambia es que ya no llama a Google
con un space o un adjunto de formato inválido.

## Módulos

### Cambia: `src/services/`

| Archivo | Cambio | RF |
|---|---|---|
| `google_chat.py` | `SPACE_NAME` y `ATTACHMENT_NAME` (regex de los formatos de la spec) con `valid_space(name)` y `valid_attachment_name(name)`. `create_message` y `download` lanzan `InvalidChatNameError` antes de hacer cualquier request si el nombre no es válido (D8). | RF-01, RF-02, RF-06, RF-07 |
| `google_chat_reply.py` | `deliver` trata un space inválido como el que falta: `logger.error` sin el valor y sale antes de pedir el token. La descarga de `read_files` no cambia: el error de `download` lo absorbe `read_one` como "no se pudo leer". | RF-03, RF-07 |
| `whatsapp_handoff.py` | `publish_notice` valida `whatsapp_notify_space` justo después de leerla: si no es válida, `logger.error` sin el valor y `None`, sin pedir token ni reintentar. | RF-04 |
| `faq_learning.py` | `publish_proposal` valida el space que saca del hilo: si no es válido, `logger.error` sin el hilo y sale sin publicar. | RF-05 |
| `knowledge_card.py` | Función privada `loggable(value)`, que quita los caracteres no imprimibles y además `\r` y `\n` con `replace`. Se aplica a `proposal_id` en los dos `logger.error` de `handle_click` (D10). | RF-08, RF-09 |

### Cambia: `src/utils/exceptions/google_chat.py`

| Archivo | Cambio | RF |
|---|---|---|
| `google_chat.py` | `InvalidChatNameError(kind)`: el mensaje dice si es un space o un adjunto, sin el valor recibido. | RF-03 a RF-05, RF-07 |

### Infraestructura

| Archivo | Cambio |
|---|---|
| `Dockerfile` | Las etapas `build` y final pasan a `python:3.14-alpine@sha256:…` (D1, D2). La etapa final quita los módulos `_curses*` y `readline` de `lib-dynload` y recalcula con `scanelf` las librerías de runtime, como el Dockerfile oficial, para que salgan `ncurses-libs` y `readline`. Crea el usuario con `adduser -S -D -H app` y borra `apk-tools` como último paso (D3, D4). La etapa `uv`, el `HEALTHCHECK`, el `ENTRYPOINT` y el `CMD` no cambian. |
| `.github/workflows/release.yaml` | En `Scan Image`, por plataforma, se mantiene la corrida que escribe `grype-<arch>.json` para el gate y el SBOM, y se agrega una segunda corrida `--only-fixed -o sarif=grype-<arch>.sarif`. Las categorías `sca-image-amd64` y `sca-image-arm64` no cambian (D6, D7). |
| `.github/workflows/sca.yaml` | Mismo cambio en `Scan Lockfile`: JSON completo para `ci/sca_gate.py` y SARIF `--only-fixed`, en la categoría `sca-lockfile` (D6, D7). |
| `README.md` | CI/CD: qué hallazgos llegan a code scanning; Dependabot: la imagen base es `python:3.14-alpine`; Hallazgos: los que no tienen fix solo quedan en el resumen del job. |
| `SECURITY.md` | "Análisis de seguridad en CI": la imagen es Alpine sin gestor de paquetes y Security muestra solo hallazgos con versión corregida (más todo lo de CodeQL). |
| `tests/` | Casos nuevos en `google_chat_test.py`, `google_chat_reply_test.py`, `whatsapp_handoff_test.py`, `faq_learning_test.py` y `knowledge_card_test.py`, con sus fakes y `httpx.MockTransport` actuales. |
| `pyproject.toml` / `uv.lock` | 3.10.8 al cerrar la spec, en un solo commit (`AGENTS.md`). |

## Modelo de datos

Sin cambios: no se tocan `src/models/` ni las migraciones, y `uv run alembic check` debe seguir sin diferencias.

## Decisiones

| # | Decisión | Alternativa descartada | Por qué |
|---|---|---|---|
| D1 | `python:3.14-alpine` fijada por digest en las etapas `build` y final. | Docker Hardened Images (`dhi.io/python:3.14`) o Chainguard (`cgr.dev/chainguard/python:latest`). | Decisión del owner. Alpine es oficial de Docker Hub, no pide login en el release, en Dependabot ni en builds locales (RF-17), y fija Python 3.14 (constitución §1). Chainguard gratis solo publica `latest`, que puede cambiar de minor, y distroless no tiene 3.14. |
| D2 | La etapa `build` también es Alpine y `uv sync --locked` instala las ruedas `musllinux` de `uv.lock`. `protobuf` usa su rueda `py3-none-any`, porque no publica `musllinux` para cp314. | Etapa `build` con glibc y venv copiado a Alpine, o compilar `protobuf` con `build-base`. | Un venv glibc no carga en musl. Las 23 dependencias compiladas tienen rueda `musllinux` para `x86_64` y `aarch64`, y `protobuf` tiene la pura, así que no hace falta compilador ni dependencia nueva (§1). |
| D3 | La etapa final quita los módulos de Python que enlazan ncurses y readline, recalcula las librerías de runtime con `scanelf` (como el Dockerfile oficial) y borra `apk-tools` al final. | Dejar `python:3.14-alpine` tal cual, o copiar `/usr/local` de Python a un `alpine` pelado. | RF-10 prohíbe ncurses y el gestor de paquetes, y `ncurses-libs` es dependencia de `.python-rundeps`. Copiar Python a mano obliga a mantener la lista de librerías, mientras que `scanelf` la deduce de los binarios. La API no usa `curses` ni `readline`. |
| D4 | `adduser -S -D -H app` de busybox. | Instalar `shadow` para seguir usando `useradd`. | Alpine no trae `useradd`. Instalar `shadow` agrega un paquete que RF-10 busca quitar. El usuario sigue siendo de sistema y sin home (RF-15). |
| D5 | `entrypoint.sh` sigue igual: corre con el `sh` de busybox. | Reescribir el entrypoint en Python. | Alpine trae `/bin/sh`, así que `set -e`, `alembic upgrade head` y `exec "$@"` cumplen RF-13 y RF-14 como hoy. Reescribirlo solo hacía falta con una base sin shell. |
| D6 | Dos corridas de Grype por objetivo: la de siempre a JSON (gate y resumen) y otra con `--only-fixed` a SARIF. | Filtrar el SARIF en `ci/sca_gate.py`, o pasar `--only-fixed` a la corrida única. | Con `--only-fixed` en la corrida única, el gate y el resumen pierden los hallazgos sin fix (RF-20, RF-21). Filtrar SARIF ata el gate, que solo usa la biblioteca estándar y se prueba sobre JSON, a un segundo formato. La base de Grype está en caché y la segunda corrida cuesta segundos. |
| D7 | Las categorías SARIF no cambian (`sca-image-amd64`, `sca-image-arm64`, `sca-lockfile`), así GitHub cierra las alertas que el análisis nuevo ya no trae. | Descartar a mano por API las 322 alertas como *won't fix*. | RF-22 se cumple sin pasos manuales y sigue cumpliéndose con cada CVE nuevo sin fix. Descartarlas a mano habría que repetirlo cada vez. |
| D8 | La validación vive en `google_chat.py`: los helpers, y `create_message`/`download` lanzan antes del request. Quien llama valida primero para escribir su propio log. | Un validador de Pydantic en `ChatSpace` o `AttachmentDataRef` (`interfaces/google_chat.py`). | El validador de Pydantic rechaza el evento entero con 422, que la spec descartó, y no cubre la property ni el hilo (RF-04, RF-05). Validar en el sink deja una sola barrera, también la que ve CodeQL. |
| D9 | Los logs nuevos nunca incluyen el space, el hilo, la property ni el nombre del adjunto rechazados. | Registrar el valor para depurar. | RF-03 a RF-05 lo prohíben, y un valor armado por un tercero es justo lo que la log injection explota (§7, `SECURITY.md` → Logs). |
| D10 | `loggable()` limpia el `proposal_id` solo para el log; la búsqueda no cambia. | Validar que el id sea UUID y responder `NO_EXISTE` antes de ir a la base. | RF-09 exige el comportamiento actual, y los tests y las tarjetas usan ids como `p-1`. El `replace` de `\r` y `\n` es el sanitizador que reconoce `py/log-injection`, y el filtro de no imprimibles cubre el resto de RF-08. |
| D11 | El "0 críticas y altas" (RF-11) se verifica antes del PR: build local de las dos plataformas y Grype v0.120.1 (el del CI) sobre la imagen. | Escanear la imagen en cada PR. | El escaneo del release solo corre en `main`, y escanear en los PRs ya se descartó en la spec 012 y aquí. Verificar antes evita fusionar una base que no cumple RF-11. **Resultado (T07, 2026-10-28):** con `python:3.14-alpine@sha256:bc62fbcb…` (Alpine 3.24.3, Python 3.14.10), 0 críticas y 0 altas en `amd64` y `arm64`; quedan 5 medias por plataforma: CVE-2025-15367 y CVE-2026-12345 del binario `python` (Grype da como fix 3.15) y CVE-2025-60876 en `busybox`, `busybox-binsh` y `ssl_client`, sin fix. Antes eran 161 por plataforma. |

## Cobertura de RF

| RF | Dónde | Tests |
|---|---|---|
| RF-01, RF-02 | `google_chat.create_message`, `download` | U-GC-1, U-GC-3, U-GC-5 |
| RF-03 | `google_chat_reply.deliver` | U-GR-1, U-GR-2 |
| RF-04 | `whatsapp_handoff.publish_notice` | U-WH-1, U-WH-2 |
| RF-05 | `faq_learning.publish_proposal` | U-FL-1 |
| RF-06, RF-07 | `google_chat.download` + `attachments.read_one` | U-GC-2, U-GC-4, U-GR-3, E6 |
| RF-08 | `knowledge_card.loggable` | U-KC-1 |
| RF-09 | `knowledge_card.handle_click` | Tests actuales de `knowledge_card_test.py`, U-KC-2 |
| RF-10 | `Dockerfile` | E1, E3 |
| RF-11 | `Dockerfile` (base) | E3, E5 |
| RF-12 | `release.yaml` (sin cambio de build ni SBOM) | E5 |
| RF-13, RF-14 | `Dockerfile` + `entrypoint.sh` | E1, E2 |
| RF-15, RF-16 | `Dockerfile` (`adduser`, `HEALTHCHECK`) | E1, `tests/health_test.py` |
| RF-17 | `Dockerfile` (`tag@digest`) + `.github/dependabot.yml` (sin cambio) | E7 |
| RF-18 | `release.yaml` | E4, E5 |
| RF-19 | `sca.yaml` | E4 |
| RF-20, RF-21 | Corrida JSON + `ci/sca_gate.py` sin cambios | `tests/ci_sca_gate_test.py` (actual), E4 |
| RF-22 | Categorías SARIF sin cambio | E5 |
| RF-23 | `codeql.yaml` sin cambios | E4, E5 |

## Estrategia de tests

**Unit** (`uv run pytest`, sin red: `httpx.MockTransport` y los fakes de cada archivo):

- **U-GC-1** — `valid_space` acepta `spaces/DM1` y `spaces/AAAA_b-1`. Rechaza `spaces/../x`, `spaces/a/b`, `spaces/a?x`, `spaces/a%2F`, `users/x`, `""` y `spaces/`.
- **U-GC-2** — `valid_attachment_name` acepta `spaces/DM1/attachments/A1` y un token opaco con `=`, `+` y `_`. Rechaza `/spaces/x`, `spaces/../x`, `a/./b`, `a?b`, `a#b`, `a%2e`, `a b` y `a\nb`.
- **U-GC-3** — `create_message` con un space inválido lanza `InvalidChatNameError` y el `MockTransport` no recibe ningún request. El mensaje de la excepción no contiene el valor.
- **U-GC-4** — `download` con un nombre inválido lanza `InvalidChatNameError` sin ningún request.
- **U-GC-5** — `create_message` con `spaces/DM1` y un hilo hace POST a `https://chat.googleapis.com/v1/spaces/DM1/messages` con el body y el `messageReplyOption` de hoy.
- **U-GR-1** — `deliver` con un space inválido no pide token ni llama a `create_message`. Escribe un `ERROR` que no contiene el valor (`caplog`).
- **U-GR-2** — `deliver` con un space válido publica como hoy (regresión de RF-02).
- **U-GR-3** — Un mensaje con dos adjuntos, uno inválido y otro válido: el inválido queda "no se pudo leer" sin request y el válido se lee.
- **U-WH-1** — `publish_notice` con `whatsapp_notify_space = "spaces/../x"` devuelve `None`. No llama a `chat_token` ni a `create_message` y escribe un solo `ERROR`, sin el valor y sin reintentos.
- **U-WH-2** — Con un space válido, el aviso se publica como hoy (los tests actuales de reintentos siguen pasando).
- **U-FL-1** — `publish_proposal` con un hilo `spaces/a?b/threads/t` no publica y escribe un `ERROR` sin el hilo.
- **U-KC-1** — `handle_click` con `proposal = "p-1\nFALSO linea"` y `get_proposal` fallando: el log tiene una sola línea por error y no contiene `\n` ni `\r` dentro del id.
- **U-KC-2** — `handle_click` con `p-1` aplica y descarta como hoy (lo cubren los tests actuales; se agrega un caso con un id que trae caracteres de control y no existe, que responde `NO_EXISTE`).

**E2E y verificación** (manual o en CI, porque el entorno de `pytest` no construye imágenes ni corre workflows):

- **E1** — `docker buildx build --platform linux/amd64,linux/arm64`, más correr cada plataforma contra un PostgreSQL local:
  - aplica las migraciones y `/health` responde `200`;
  - `docker inspect` muestra el healthcheck `healthy`;
  - la API corre con un uid distinto de 0;
  - `/lib/apk/db/installed` no lista `apk-tools`, `ncurses-libs`, `readline`, `perl`, `util-linux` ni `shadow`.
- **E2** — El contenedor con `DB_HOST` inalcanzable termina con código distinto de 0 y la API no levanta.
- **E3** — Grype v0.120.1 en contenedor sobre las dos plataformas de la imagen de E1: 0 críticas y 0 altas, con o sin fix. Es requisito para abrir el PR (D11).
- **E4** — En el PR:
  - `Lockfile Scan` sube un SARIF que solo trae hallazgos con fix, y el resumen del job sigue listando todos;
  - CodeQL marca #4, #2 y #3 como corregidas en el PR;
  - `Type Check & Tests` pasa.
- **E5** — En el release de `main`:
  - el escaneo de la imagen no trae críticas ni altas en ninguna plataforma, sube el SBOM, publica, despliega por digest y pasa `rollout status`;
  - después, Security no tiene alertas de CodeQL abiertas ni alertas de Grype abiertas sin fix.
- **E6** — Un mensaje de prueba con un adjunto en Google Chat recibe la respuesta diferida con el archivo leído, sobre la imagen Alpine desplegada.
- **E7** — En *Insights → Dependency graph → Dependabot*, el ecosistema `docker` lista `python:3.14-alpine` sin errores. El siguiente PR semanal de imágenes la propone por digest.

Límites de los fakes: `MockTransport` comprueba que no sale ningún request, pero no lo que haría Google con una ruta
armada. Si CodeQL reconoce o no los sanitizadores (D8, D10) solo se ve en E4.

## Orden de implementación

1. `InvalidChatNameError`, los helpers y la barrera en `create_message`/`download`, con U-GC-1 a U-GC-5.
2. Validación en los que llaman: `deliver`, `publish_notice` y `publish_proposal`, con U-GR-*, U-WH-* y U-FL-1.
3. `loggable()` en `knowledge_card.py`, con U-KC-1 y U-KC-2.
4. `Dockerfile` Alpine (D1 a D5), y E1 a E3 en local.
5. `release.yaml` y `sca.yaml` con el SARIF `--only-fixed` (D6, D7).
6. `README.md` y `SECURITY.md`.
7. Cierre: `uv run pytest`, `uv run pyright`, `uv run alembic check` y el commit `chore(release): Bump version to v3.10.8` con `uv sync`.

## Release

1. PR desde `fix/code-scanning-findings`. Los checks del ruleset (`Type Check & Tests`, `Lockfile Scan`, `Analyze (actions)`, `Analyze (python)` y la regla de code scanning) tienen que pasar, y se revisa E4.
2. Merge con merge commit `chore(release): Version 3.10.8`. `release.yaml` verifica, construye, escanea (E5), publica y despliega. No hay migraciones nuevas, y el contenedor aplica las existentes al arrancar.
3. Comprobar en el cluster que `/health` responde y que el pod corre la imagen nueva, y luego hacer E6.
4. Revisar Security (E5) y el Dependency graph (E7). Después, `/tag-release`.

## Riesgos

- **Alpine no llega a 0 altas** (busybox, openssl o musl con un CVE alto sin fix): RF-11 no se cumple. E3 lo detecta antes del PR; si pasa, se vuelve al owner con el resultado (por ejemplo, DHI como alternativa).
- **Diferencias de musl en runtime** (DNS en Kubernetes, `asyncpg`, `uvloop`, `lxml`/`pillow` para adjuntos): fallos que los tests unitarios no ven. E1 prueba la base de datos y E6 los adjuntos sobre la imagen real. Si algo falla, el rollback es revertir el merge, y el deploy anterior queda fijado por digest.
- **`protobuf` en su implementación pura**: más lenta que la compilada. Solo la usan los clientes de Google, con poco volumen, así que el impacto esperado es bajo y se observa en E6.
- **CodeQL no reconoce los sanitizadores** (D8, D10): las alertas siguen abiertas aunque el código ya las corrige. Se ve en E4. Si pasa, se descartan en Security como *false positive*, con referencia a D8/D10 y a los tests U-GC-3, U-GC-4 y U-KC-1.
- **Dependabot propone `3.15-alpine` como minor** (el grupo `docker-minor-patch` incluye minors): rompería §1. Ya pasa hoy con `slim`. Ese PR se cierra sin fusionar, y queda para otra spec agregar un `ignore` en `dependabot.yml`.
- **Quitar `apk-tools` impide depurar con `apk add`** dentro del contenedor: es intencional (RF-10). Para depurar se usa una imagen efímera o `kubectl debug`.
