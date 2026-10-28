# Plan 014 — Limpieza de las alertas de code scanning

- **Spec:** [spec.md](spec.md) (aprobada 2026-10-28)
- **Status:** Approved
- **Version:** 3.11.0 (minor sobre la 3.10.8 de `main`: cambia el runtime de Python; si Dependabot fusiona antes, el
  minor siguiente)

Respeta la constitución en lo que no cambia. No agrega dependencias de Python (§1): `ormsgpack` y `uuid-utils` solo suben
de versión dentro de lo que ya permite `uv.lock`. Mantiene las capas, porque la barrera vive en
`services/google_chat.py` (§2). Pyright y los tests siguen en verde con 3.15 (§3, §4). No toca `src/models/` ni las
migraciones (§5), y la imagen sigue arrancando sola con `/health` (§10). La spec ya está aprobada, pero el plan necesita
estas aprobaciones antes de implementar:
- §1 de la constitución y `AGENTS.md`, que pasan a Python 3.15;
- `uv.lock`, que se regenera en una tarea intermedia;
- `Dockerfile`, con la base nueva y `build-base` solo en la etapa `build`;
- `test.yaml` y `release.yaml`, con el uv del CI en 0.13.6.

## Prueba previa (2026-10-28, en una copia del repo en el scratchpad)

- `uv lock --upgrade-package ormsgpack --upgrade-package uuid-utils --upgrade-package websockets` con
  `requires-python = ">=3.15"` cambia solo `ormsgpack` (1.12.2 → 1.13.0) y `uuid-utils` (0.17.1 → 0.19.0, rueda
  `abi3`). `websockets` se queda en 16.1.1 porque lo limita otra dependencia, y no publica rueda cp315.
- `pyyaml` 6.0.3, `zstandard` 0.25.0 y `websockets` 16.1.1 compilan desde el sdist en macOS y en
  `python:3.15-alpine` (arm64) con `build-base`. Tardan unos 70 s, y sus `.so` solo enlazan `libc.musl`. `pyyaml` queda
  sin libyaml (`__with_libyaml__ = False`).
- Con Python 3.15.2, `uv run pytest` pasa con 475 tests (1 `DeprecationWarning` de `google-genai` sobre 3.17) y
  `uv run pyright` termina con 0 errores.
- uv 0.12.23, el que fija hoy el CI, solo puede descargar `3.15.0rc3`. uv 0.13.6, el de la etapa `uv` del
  `Dockerfile`, descarga 3.15.2.

## Endpoints

Sin cambios (RF-09). El webhook de Google Chat responde igual. Solo cambia la forma en que `create_message` y `download`
validan el nombre antes del request.

## Módulos

### Cambia: `src/services/`

| Archivo | Cambio | RF |
|---|---|---|
| `google_chat.py` | `create_message` hace el chequeo como `if not re.fullmatch(SPACE_NAME, space): raise InvalidChatNameError("space")` en la misma función que arma la URL, en vez de llamar a `valid_space()` (D1). `download` hace lo mismo con `ATTACHMENT_NAME` (D2). `valid_space()` y `valid_attachment_name()` se quedan para quienes llaman (`google_chat_reply`, `whatsapp_handoff`, `faq_learning`), que no cambian. | RF-01 a RF-04 |

### Infraestructura

| Archivo | Cambio |
|---|---|
| `.python-version` | `3.15` (D3). |
| `pyproject.toml` | `requires-python = ">=3.15"` (D3). La versión sube a 3.11.0 solo en el commit final. |
| `uv.lock` | Regenerado con `uv lock --upgrade-package ormsgpack --upgrade-package uuid-utils --upgrade-package websockets`. Diff esperado: `requires-python`, las ruedas cp314 que salen y las dos versiones de la prueba previa (D4). |
| `Dockerfile` | Las etapas `build` y final pasan a `python:3.15-alpine@sha256:…` (D5). La etapa `build` agrega `apk add --no-cache build-base` antes de `uv sync --locked` (D6). En la final, la ruta de `lib-dynload` pasa a `python3.15`, y el resto (scanelf, `adduser`, `apk del apk-tools`, `HEALTHCHECK`, `ENTRYPOINT`, `CMD`) no cambia. Se actualizan los comentarios que citan la spec 013. |
| `.github/workflows/test.yaml`, `release.yaml` | `astral-sh/setup-uv` con `version: 0.13.6`, alineado con la etapa `uv` del `Dockerfile` (D7). |
| `README.md` | Requisitos: Python 3.15. Se reemplaza la nota de la línea 27 por una sobre `pyyaml`, `zstandard` y `websockets`, que se compilan desde el código fuente y piden compilador de C (Xcode CLT en macOS). La sección de `uv sync` (línea 91) pasa a 3.15. En Dependabot (línea 284), la base es `python:3.15-alpine` y el PR que se cierra sin fusionar es el de `3.16-alpine` (D8). |
| `SECURITY.md` | La imagen es `python:3.15-alpine`. |
| `docs/constitution.md` §1, `AGENTS.md` | "Python 3.14" pasa a "Python 3.15" (D8, aprobación pendiente). |
| `tests/google_chat_test.py` | Casos nuevos U-GC-6 y U-GC-7. U-GC-3 y U-GC-4 (spec 013) siguen como regresión. |

`codeql.yaml`, `sca.yaml`, `.github/codeql/codeql-config.yaml`, `ci/sca_gate.py`, `.github/sca-exceptions.json`,
`.github/dependabot.yml` y `entrypoint.sh` no cambian (RF-14, RF-17 a RF-19).

## Modelo de datos

Sin cambios. No se tocan `src/models/` ni las migraciones, y `uv run alembic check` debe seguir sin diferencias.

## Decisiones

| # | Decisión | Alternativa descartada | Por qué |
|---|---|---|---|
| D1 | La barrera de `create_message` es `if not re.fullmatch(SPACE_NAME, space): raise …`, escrita en la misma función que el request. | Seguir con `if not valid_space(space)`, o declarar un *barrier* con un model pack de CodeQL (`request-forgery`). | CodeQL solo trata como barrera una llamada a `re.match`/`re.fullmatch` (o al `.fullmatch` de un `re.compile`) que sea la condición del `if` en la misma función (`stringRestriction` en `ServerSideRequestForgeryCustomizations.qll`). No sigue el resultado a través de `valid_space()` ni de `is not None`, y por eso la #4 sigue abierta. El *barrier* de models-as-data es un `FullUrlControlSanitizer`: no aplica a `py/partial-ssrf`. Se usa `re.fullmatch(...)` del módulo en vez de `SPACE_NAME.fullmatch(...)`, para no depender de que CodeQL rastree la variable global hasta `re.compile` (RF-01). |
| D2 | `download` usa la misma forma con `ATTACHMENT_NAME`. | Dejarla como está porque hoy no tiene alerta. | Es el mismo sink con el token de la cuenta de servicio (RF-02). Si CodeQL empieza a modelar el adjunto como fuente, la alerta no reaparece. El cambio es de una línea y lo cubre U-GC-4. |
| D3 | `requires-python = ">=3.15"` y `.python-version = 3.15`. | Dejar `>=3.14` para que el lock sirva también en 3.14. | RF-05 pide 3.15 en desarrollo, CI e imagen, y la constitución fija una sola versión. Con `>=3.14`, un entorno en 3.14 seguiría funcionando sin avisar y Grype volvería a ver las CVEs si alguien construye con esa base. |
| D4 | `uv lock --upgrade-package` solo para `ormsgpack`, `uuid-utils` y `websockets`. | `uv lock --upgrade` de todo el árbol. | La spec deja fuera actualizar más de lo que exige 3.15. Según la prueba previa, solo cambian dos paquetes con rueda cp315/abi3, lo que reduce la revisión del PR y el riesgo de regresión. |
| D5 | `python:3.15-alpine@sha256:…` (tag de minor más digest) en las dos etapas. | `python:3.15.2-alpine`, con tag de patch. | Es la misma convención de la spec 013: Dependabot propone el digest cuando el tag de minor se mueve, y así llegan los patches de 3.15 (RF-06, RF-14). |
| D6 | `build-base` solo en la etapa `build`, y `pyyaml` sin libyaml. | Instalar `yaml-dev` para compilar `_yaml`, o forzar las implementaciones puras sin compilador. | `zstandard` no tiene backend puro: sin compilador, `uv sync` falla (RF-07). El compilador no pasa a la etapa final (RF-08), y los `.so` solo enlazan musl, así que el `scanelf` de la final no suma librerías. libyaml agregaría una librería de runtime a la imagen final para un uso marginal de YAML en `langchain-core`. |
| D7 | `setup-uv` en `version: 0.13.6` en `test.yaml` y `release.yaml`. | Mantener 0.12.23 y fijar `python-version: 3.15.2` en el action. | uv 0.12.23 no conoce 3.15.2 y usaría `3.15.0rc3`, una prerelease que además está afectada por la CVE. Alinearlo con la etapa `uv` del `Dockerfile` es lo que ya pide el README (línea 284), y deja una sola fuente de la versión de Python (`.python-version`). |
| D8 | La documentación de 3.15 (constitución, `AGENTS.md`, README y `SECURITY.md`) va en la misma tarea que el cambio de runtime. | Actualizar la documentación al final, junto con el cambio de versión. | Mientras la constitución diga 3.14, el código incumple §1. Un solo commit deja la rama coherente en cada paso. Requiere la aprobación del owner para `docs/` y `AGENTS.md`. |
| D9 | Antes de abrir el PR, se escanea con Grype v0.120.1 en contenedor la imagen local de las dos plataformas, igual que el T07 de la spec 013. Si aparece un hallazgo con fix, se corrige en la rama. | Esperar al escaneo del release. | RF-16 pide Security en 0 al cerrar. Un hallazgo que se descubre recién en el release obliga a otro PR y a otra versión. Correr `anchore/grype` necesita la aprobación del owner, como en la spec 013. |
| D10 | Si CodeQL del PR sigue reportando `py/partial-ssrf`, la barrera pasa a la forma positiva (`if re.fullmatch(...):` con el request dentro y el `raise` después). | Descartar la alerta a mano. | La spec descarta el descarte manual (RF-01). La forma positiva no depende de cómo CodeQL trata el `not`, y la regresión la cubren los mismos tests. |

## Cobertura de RF

| RF | Dónde | Tests |
|---|---|---|
| RF-01 | `google_chat.create_message` (D1, D10) | E4 (CodeQL del PR), E5 (alerta #4 cerrada en `main`) |
| RF-02 | `create_message` y `download` con `CHAT_API_URL`/`CHAT_MEDIA_URL` fijos | U-GC-5, U-GC-6, test actual de `download` |
| RF-03 | `create_message` | `test_create_message_publica_en_el_space_sin_hilo`, `test_e11_…_en_el_hilo`, U-GC-6 |
| RF-04 | `create_message` y los que llaman (sin cambios) | U-GC-3, U-GC-7, U-GR-1, U-WH-1, U-FL-1 (spec 013) |
| RF-05 | `.python-version`, `pyproject.toml`, `setup-uv` 0.13.6, `Dockerfile` | E1 (`python --version` en la imagen), E4 (log de `Type Check & Tests`), `uv run python --version` en local |
| RF-06 | `Dockerfile` (D5) | E1, E7 |
| RF-07 | `Dockerfile` (D6) + `uv.lock` (D4) | E1 en `amd64` y `arm64` |
| RF-08 | `Dockerfile`: etapa final sin cambios de paquetes | E1 (`gcc`, `apk`, ncurses y readline ausentes) |
| RF-09 | Sin cambios en `routers` ni `interfaces` | `uv run pytest` completo con 3.15 |
| RF-10, RF-11 | `entrypoint.sh` sin cambios | E1, E2 |
| RF-12, RF-13 | `Dockerfile` (`adduser`, `HEALTHCHECK`) | E1, `tests/health_test.py` |
| RF-14 | `Dockerfile` (`tag@digest`) + `dependabot.yml` sin cambios | E7 |
| RF-15 | Base 3.15 (D5) | E3, E5 |
| RF-16 | D9 + release | E3, E5 |
| RF-17 | `release.yaml`/`sca.yaml` con `--only-fixed` sin cambios | E4, E5 |
| RF-18 | `codeql.yaml` sin cambios | E4, E5 |
| RF-19 | `ci/sca_gate.py` sin cambios | `tests/ci_sca_gate_test.py` (actual), E4 |

## Estrategia de tests

**Unit** (`uv run pytest`, sin red, con `mock_http` y `httpx.MockTransport` de `tests/google_chat_test.py`):

- **U-GC-6** — `create_message("spaces/AAA_b-1", …, thread="spaces/AAA_b-1/threads/t")` hace POST a
  `https://chat.googleapis.com/v1/spaces/AAA_b-1/messages` con `messageReplyOption` y el hilo en el body. El host del
  request es `chat.googleapis.com`.
- **U-GC-7** — `create_message` con cada uno de `spaces/../x`, `spaces/a/b`, `spaces/a?x`, `spaces/a%2F`,
  `https://evil.test/x`, `spaces/AAA\n` y `""` lanza `InvalidChatNameError`, `seen` queda vacío y el mensaje no contiene
  el valor. `\n` al final cubre la diferencia entre `re.match` con `$` y `fullmatch`.
- **Regresión:** U-GC-1 a U-GC-5, U-GR-1 a U-GR-3, U-WH-1, U-WH-2 y U-FL-1 (spec 013), y los 475 tests actuales, ahora
  sobre Python 3.15.

**E2E y verificación** (manual o en CI, porque `pytest` no construye imágenes ni corre CodeQL):

- **E1** — `docker buildx build --platform linux/amd64,linux/arm64`, más correr cada plataforma contra un PostgreSQL
  local:
  - `python --version` responde `3.15.x`, se aplican las migraciones y `/health` responde `200`;
  - el healthcheck queda `healthy` y el uid es distinto de 0;
  - `gcc`, `apk` y `/lib/apk/db/installed` con `apk-tools`, `ncurses-libs`, `readline` o `build-base` no aparecen en la
    etapa final;
  - `python -c "import yaml, zstandard, websockets, asyncpg, greenlet, uvloop"` funciona.
- **E2** — Con `DB_HOST` inalcanzable, el contenedor termina con código distinto de 0 y la API no levanta.
- **E3** — Grype v0.120.1 en contenedor (D9) sobre las dos plataformas de E1: 0 hallazgos con fix y ninguna de las
  CVE-2025-15367 y CVE-2026-12345. Es requisito para abrir el PR.
- **E4** — En el PR:
  - `Analyze (python)` ya no reporta `py/partial-ssrf` en `google_chat.py` (si lo reporta, se aplica D10);
  - `Type Check & Tests` corre con 3.15.2;
  - `Lockfile Scan` no trae hallazgos con fix.
- **E5** — En el release de `main`:
  - escanea, sube el SBOM, publica, despliega por digest y pasa `rollout status`;
  - después de ese release y del CodeQL de `main`, Security tiene 0 alertas abiertas, y #4, #34, #44, #196 y #206
    quedan como *fixed*.
- **E6** — Un mensaje de prueba en Google Chat recibe la respuesta diferida en su hilo, sobre la imagen 3.15
  desplegada.
- **E7** — En *Insights → Dependency graph → Dependabot*, el ecosistema `docker` lista `python:3.15-alpine` sin errores.

Límites: `MockTransport` comprueba la URL y que no salga ningún request, pero no cómo razona CodeQL. Que la #4 se cierre
(RF-01) solo se ve en E4 y E5. El tiempo de compilación bajo QEMU (`arm64` en el runner `amd64`) solo se mide en el
release.

## Orden de implementación

1. Barrera de `create_message` y `download` (D1, D2), con U-GC-6, U-GC-7 y la regresión, todavía en 3.14.
2. Runtime 3.15: `.python-version`, `requires-python` y `uv.lock` (D3, D4). `rm -rf .venv && uv sync`, `pytest` y
   `pyright` con 3.15.
3. `setup-uv` 0.13.6 en `test.yaml` y `release.yaml` (D7).
4. `Dockerfile` 3.15 con `build-base` en `build` (D5, D6), y E1 y E2 en las dos plataformas.
5. Prescan con Grype (E3, D9), y corrección en la rama si aparece algo con fix.
6. Constitución, `AGENTS.md`, `README.md` y `SECURITY.md` (D8).
7. Cierre: `uv run pytest`, `uv run pyright`, `uv run alembic check` y el commit
   `chore(release): Bump version to v3.11.0` con `uv sync`.

## Release

1. PR desde `fix/code-scanning-python-315`. Tienen que pasar los checks del ruleset (`Type Check & Tests`,
   `Lockfile Scan`, `Analyze (actions)`, `Analyze (python)` y la regla de code scanning), y se revisa E4.
2. Merge con merge commit `chore(release): Version 3.11.0`. `release.yaml` verifica, construye (con más tiempo de build
   por la compilación en `arm64`), escanea, publica y despliega. No hay migraciones nuevas, y el contenedor aplica las
   existentes al arrancar.
3. Comprobar en el cluster que `/health` responde y que el pod corre la imagen nueva. Después, E6.
4. Esperar al CodeQL de `main` (push o el cron diario) y revisar Security (E5) y el Dependency graph (E7). Después,
   `/tag-release`.

## Riesgos

- **La compilación de `zstandard`, `pyyaml` y `websockets` bajo QEMU alarga el job `package`.** Tarda unos 70 s nativo en
  arm64, y puede tomar varios minutos emulado. Hoy el job tiene un timeout de 45 min. Se mide en E4/E5. Si se acerca al
  límite, se vuelve al owner antes de tocar el timeout.
- **CodeQL no reconoce la barrera de D1.** Se ve en E4 y se aplica D10. Si tampoco basta, se vuelve al owner, porque la
  spec no permite descartarla.
- **3.15.2-alpine trae CVEs propias con fix** (de Python, musl, busybox u openssl). E3 las detecta antes del PR. Se
  corrigen con el digest más reciente. Si no tienen corrección aplicable, se vuelve al owner (decisión "Si la migración
  resulta inviable").
- **Diferencias de comportamiento de 3.15 en runtime** (asyncpg, greenlet, uvloop, LangGraph): los 475 tests pasan,
  pero no tocan una base de datos real ni el LLM. E1 prueba la base de datos y E6 el flujo completo. Rollback: revertir
  el merge, y el deploy anterior queda fijado por digest.
- **`google-genai` usa `_UnionGenericAlias`, que se elimina en Python 3.17.** Hoy solo es un `DeprecationWarning`. Queda
  para cuando Dependabot proponga una versión nueva o para la migración a 3.17.
- **Desarrollo local sin compilador de C:** `uv sync` falla al compilar `zstandard`. El README lo documenta (Xcode CLT en
  macOS, `build-essential` en Linux).
- **Dependabot propondrá `3.16-alpine` como minor** (grupo `docker-minor-patch`). Ese PR se cierra sin fusionar, como
  documenta el README, hasta que haya una spec de migración.
