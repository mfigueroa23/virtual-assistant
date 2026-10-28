# Plan 012 — DevSecOps: CodeQL, Dependabot y SCA

- **Spec:** [spec.md](spec.md) (approved 2026-10-27)
- **Status:** Approved
- **Version:** 3.10.0 (feat → minor, desde 3.9.0)

El plan porta a Python la implementación de la spec 009 del portafolio (`Portfolio/web`), que ya está en producción
con PRs de Dependabot bumpeados, y la adapta a `uv`. El plan respeta la constitución:
- §1: no agrega dependencias. CodeQL, Grype y Syft corren en el CI como actions o CLIs fijados; los scripts nuevos usan
  solo la biblioteca estándar (`tomllib`, `json`, `subprocess`).
- §3 y §4: los scripts entran en `pyright` y sus tests en `tests/`, sin red, BD ni LLM; `test.yaml` los ejecuta.
- §5: sin cambios en `src/models/` ni migraciones.
- §6: los secretos del CI no llegan a ejecuciones de Dependabot ni a reportes.
- §9 y §10: `test.yaml` sigue bloqueando `main`; el release sigue fijando la imagen por digest, ahora escaneada antes de
  llegar a Docker Hub.

La aprobación de infra/CI (workflows nuevos y cambios en `.github/`, `release.yaml` y `Dockerfile`) la dio el owner
el 2026-10-28. **Pendientes**, como acciones del owner en **Release**: instalar la App `portfolio-version-bump` en este
repo, desactivar el *default setup* de CodeQL y activar el ruleset de `main` (D14); y, si el pre-escaneo lo exige, la
aprobación de cambios en `uv.lock` (D13).

## Modules

### New: `.github/workflows/codeql.yaml` (`CodeQL`)

| File | Responsibility | RF |
|---|---|---|
| `codeql.yaml` | Disparadores: `pull_request` a `main`, `push` a `main`, `schedule` diario (`0 7 * * *` UTC) y `workflow_dispatch`. Job `Analyze (${{ matrix.language }})` con matriz `actions` y `python`, `build-mode: none` (no instala nada, D1); `github/codeql-action/init` con `config-file: ./.github/codeql/codeql-config.yaml` y luego `analyze` (sube el SARIF). Permisos: `contents: read`, `security-events: write`, `actions: read`. Actions fijadas por SHA con comentario `# vX.Y.Z` (mismos pins que el portafolio). | RF-01–RF-05, RF-41 |

### New: `.github/codeql/codeql-config.yaml`

| File | Responsibility | RF |
|---|---|---|
| `codeql-config.yaml` | `queries: [uses: security-extended]`; `paths-ignore`: `.venv`, `**/__pycache__`. | RF-01, RF-08 |

### Changed: `.github/dependabot.yml`

| File | Change | RF |
|---|---|---|
| `dependabot.yml` | Se reescribe (reemplaza la configuración de la spec 010). Tres entradas en `/`: `uv`, `github-actions`, `docker`. Cada una con `schedule` semanal, lunes 06:00 `America/Santiago`; `groups.<eco>-minor-patch` con `applies-to: version-updates` y `update-types: [minor, patch]`, así los mayores quedan uno por PR; `commit-message.prefix` literal `build(deps)` (`uv`, también `prefix-development`; `docker`) o `ci(deps)` (`github-actions`), sin `include: scope` (D9); `labels: [dependencies, python:uv \| github-actions \| docker]` (D10). Sin `open-pull-requests-limit: 0` ni auto-merge. | RF-10–RF-15, RF-17 |

### New: `.github/workflows/sca.yaml` (`SCA`)

| File | Responsibility | RF |
|---|---|---|
| `sca.yaml` | Disparadores: `pull_request` a `main`, `schedule` diario (`30 7 * * *` UTC), `workflow_dispatch`. Job `Lockfile Scan`: checkout sin instalar (D1), `anchore/scan-action/download-grype` (fijado, `grype-version: v0.120.1`, `cache-db: true`), `grype file:uv.lock -o json=grype.json -o sarif=grype.sarif` (D4), `python3 ci/sca_gate.py --title Lockfile --report grype.json` (resumen y código de salida, D5) y `upload-sarif` con `category: sca-lockfile` bajo `if: always()` y solo si el PR no viene de un fork. Usa el `python3` del runner: el gate no necesita el venv. Permisos: `contents: read`, `security-events: write`. | RF-26–RF-30, RF-36–RF-38, RF-41 |

### New: `ci/sca_gate.py`

| File | Responsibility | RF |
|---|---|---|
| `ci/sca_gate.py` | Puerto de `scripts/sca-gate.mjs` del portafolio. CLI: `--report <grype.json>` (repetible), `--exceptions` (por defecto `.github/sca-exceptions.json`), `--title`. Funciones puras: `validate_exceptions(entries, today)` → `ExceptionSets(valid, expired, invalid)` (una entrada necesita `id`, `reason` no vacío y `expires` ISO; vale hasta ese día UTC inclusive); `classify(match, exceptions)` → `"blocking" \| "excepted" \| "reported"` (bloquea `Critical`/`High` con `fix.state == "fixed"`; la excepción coincide con `vulnerability.id` o con cualquier `relatedVulnerabilities[].id`, así un GHSA cubre su CVE); `findings(report, exceptions)` → filas sin duplicados; `summary(sections, exception_sets)` → tabla Markdown (paquete, instalada, corregida en, severidad, advisory, estado) más las excepciones vencidas e inválidas. Escribe en `$GITHUB_STEP_SUMMARY` si existe y en stdout. Sale con 1 ante un hallazgo bloqueante, una excepción inválida o un reporte ilegible, con un mensaje de una línea y sin traza. `SCA_GATE_TODAY` fija la fecha en los tests. Textos del resumen en español (§8). | RF-28–RF-30, RF-33, RF-36–RF-38 |

### New: `.github/sca-exceptions.json`

| File | Responsibility | RF |
|---|---|---|
| `sca-exceptions.json` | `[]` al empezar. Forma de cada entrada: `{ "id": "GHSA-…", "reason": "…", "expires": "YYYY-MM-DD" }`. Lo comparten el escaneo de `uv.lock` y el de la imagen (D5). | RF-36–RF-38 |

### New: `.github/workflows/dependabot-bump.yaml` (`Dependabot Version Bump`)

| File | Responsibility | RF |
|---|---|---|
| `dependabot-bump.yaml` | Igual al del portafolio. Job `request` en `pull_request` (`opened`, `synchronize`, `reopened`) solo si `github.event.pull_request.user.login == 'dependabot[bot]'` y el actor es Dependabot o una persona (los pushes de la App se ignoran): sin checkout, `permissions: actions: write`, ejecuta `gh workflow run dependabot-bump.yaml --ref main -f pr=<n>` (D2). Job `bump` en `workflow_dispatch` (input `pr` opcional), `push` a `main` con `paths: [pyproject.toml]` y `schedule` cada hora como respaldo (D3): checkout de `main`, `actions/create-github-app-token` (`vars.BUMP_APP_ID`, `secrets.BUMP_APP_PRIVATE_KEY`, permisos contents, pull-requests, statuses y workflows en write), `python3 ci/dependabot_bump.py [--pr N]`. `concurrency: dependabot-bump` sin cancelar. | RF-18, RF-20–RF-25, RF-41, RF-42 |

### New: `ci/dependabot_bump.py`

| File | Responsibility | RF |
|---|---|---|
| `ci/dependabot_bump.py` | Puerto de `scripts/dependabot-bump.mjs`. Funciones puras: `next_patch(v)` (`X.Y.Z` → `X.Y.(Z+1)`, `ValueError` si no); `project_version(pyproject_text)` (lee `[project].version` con `tomllib`, sin ejecutar nada); `set_pyproject_version(text, v)` (reemplaza solo el valor de la línea `version = "…"` de la tabla `[project]`, D6); `set_lock_version(text, v)` (reemplaza solo la línea `version = "…"` que sigue a `name = "virtual-assistant"` en `uv.lock`, D6); `is_bump_commit(subject)`; `decide(author, pr_version, main_version)` → `"skip" \| "noop" \| "rebump"`; `trailing_bump(commits)` (el último commit que no es bump; rechaza si hay un bump con commits después). `rebump(api, pr, head, version)` reconstruye la rama solo con la API de GitHub (D11): mueve la rama atrás para quitar bumps finales, hace merge de `main` si está atrás (`POST /merges`, 409 = conflicto) y agrega un commit con GraphQL `createCommitOnBranch` (`expectedHeadOid`) que cambia `pyproject.toml` y `uv.lock`; si falla, restaura el head original salvo que otro haya hecho push. `GithubApi` implementa esas llamadas con `gh` vía `subprocess.run` con lista de argumentos (sin shell), nunca `git` ni `uv` (D1). El CLI lista los PRs abiertos de `app/dependabot` (o `--pr N`), pone el commit status `Version bump` y, si falla, comenta una vez por head pidiendo `@dependabot recreate` (comentario en español). | RF-18–RF-25 |

### Changed: `.github/workflows/release.yaml`

| File | Change | RF |
|---|---|---|
| `release.yaml` | Job `package` pasa a `Build, Scan and Publish Image`, `timeout-minutes: 45`, permisos `contents: read`, `security-events: write`, con un servicio `registry:3` (fijado por digest) en `localhost:5000` y Buildx con `driver-opts: network=host` y `buildkitd-config-inline` HTTP para ese registry (D7). `build-push-action` publica en `localhost:5000/virtual-assistant:${{ github.sha }}` en vez de Docker Hub. Pasos nuevos, en orden: descargar Grype y Syft (fijados); escanear `linux/amd64` y `linux/arm64` (`grype registry:… --platform …`, `GRYPE_REGISTRY_INSECURE_USE_HTTP=true`) a JSON y SARIF; `python3 ci/sca_gate.py` con los dos reportes (falla antes de publicar); `upload-sarif` por plataforma (`category: sca-image-amd64` / `sca-image-arm64`, `if: always()`); recién entonces `docker buildx imagetools create` copia el manifest list a los tags `latest`, versión y `sha-<commit>` de Docker Hub y falla si Docker Hub reporta otro digest; SBOM SPDX JSON por plataforma con Syft (`--source-name <user>/virtual-assistant:<version>`) subido como artefacto `virtual-assistant-<version>-sbom` (D8). `docker/login-action` queda antes del build solo para bajar la imagen base con cuota. El output `digest` sigue siendo el del build, que es el escaneado y publicado. `verify` y `deploy` no cambian sus pasos; `deploy` sigue con `needs: package`. Todas las actions con comentario `# vX.Y.Z`. | RF-31–RF-35, RF-40–RF-42 |

### Changed: otros archivos

| File | Change | RF |
|---|---|---|
| `.github/workflows/test.yaml` | Solo comentarios `# vX.Y.Z` en los pins que no lo tienen (`actions/checkout`). Disparadores, instalación y pasos sin cambios: `uv run pytest` ya ejecuta los tests de `ci/`. | RF-16, RF-39 |
| `Dockerfile` | `python:3.14-slim` fijado como `tag@sha256:…` en las dos etapas; uv pasa a una etapa `FROM ghcr.io/astral-sh/uv:0.12.23@sha256:… AS uv` y `COPY --from=uv /uv /bin/uv`, para que Dependabot la vea (D12). Si el pre-escaneo (paso 1 del orden) da hallazgos bloqueantes del sistema, la etapa final agrega `apt-get upgrade` (D13). | RF-10, RF-31 |
| `pyproject.toml` | `[tool.pyright].include` suma `"ci"`. `version` → `3.10.0` al cierre. | RF-41 |
| `.dockerignore` | Suma `ci` (los scripts del CI no entran en la imagen). | — |
| `README.md` | Sección **CI/CD**: los workflows nuevos en la tabla, la App y sus variables en la configuración, el párrafo de Dependabot reescrito (updates semanales y bump automático, en lugar de lo de la spec 010), el archivo de excepciones y cómo agregar una. Se corrige el nombre del deployment (`deployment/virtual-assistant`, namespace `virtual-assistant`, como en `release.yaml`). | — |
| `SECURITY.md` | Sección nueva "Análisis de seguridad en CI": qué se analiza, qué bloquea, dónde se ven los hallazgos y cómo se pide una excepción. | — |

### Infrastructure (owner, fuera del repo)

| Item | Change |
|---|---|
| GitHub App | Instalar `portfolio-version-bump` también en `mfigueroa23/virtual-assistant`. `BUMP_APP_ID` (`5463156`) como variable de Actions y `BUMP_APP_PRIVATE_KEY` como secreto de Actions (no de Dependabot, D2). |
| Code scanning | *Default setup* desactivado (`gh api -X PATCH repos/mfigueroa23/virtual-assistant/code-scanning/default-setup -f state=not-configured`). |
| Ruleset de `main` | El ruleset existente `Branch Ruleset` (deshabilitado; hoy exige `Type Check & Tests`) suma los checks `Lockfile Scan`, `Analyze (actions)` y `Analyze (python)` y la regla de code scanning de CodeQL (`security_alerts_threshold: high_or_higher`, `alerts_threshold: none`, D4), y se activa. Ver D14 sobre `required_signatures`. |
| Etiquetas | `github-actions` y `docker` (`dependencies` y `python:uv` ya existen). |

## Decisions

| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | Ningún job con token de escritura o secretos instala dependencias ni ejecuta código del PR: CodeQL usa `build-mode: none`, el SCA escanea el archivo `uv.lock`, y el bump lee y edita texto con `tomllib`/regex y llama solo a `gh`. | Ejecutar `uv sync` antes de analizar, o bumpear con `uv version --bump patch` / `uv lock` | RF-25 y RF-42: `uv lock` puede construir sdists del PR (código arbitrario de build backends). Python no necesita build para CodeQL. |
| D2 | La ejecución disparada por Dependabot solo despacha `dependabot-bump.yaml` en `main` (`actions: write`, sin checkout); la ejecución despachada, fuera del contexto de Dependabot, usa el token de la App. | Un secreto de Dependabot con la clave de la App usado en el `pull_request` | RF-42 prohíbe secretos en ejecuciones de PRs de Dependabot; la despachada corre el workflow y el script de `main`, no los del PR (RF-25). |
| D3 | El push del bump lo hace la App `portfolio-version-bump` (reusada, decisión del owner), más un disparador `push` a `main` (`paths: pyproject.toml`) y uno cada hora que revisan todos los PRs abiertos de Dependabot. | Hacer push con `GITHUB_TOKEN`, o una App nueva solo para este repo | Los commits con `GITHUB_TOKEN` no disparan workflows, así que el head bumpeado quedaría sin `test.yaml`/CodeQL/SCA y el ruleset lo bloquearía. El `push` cubre RF-20; el horario cubre un dispatch perdido. Reusar la App evita otra clave privada que rotar. |
| D4 | Grype para vulnerabilidades (`uv.lock` e imagen) y Syft para SBOM, como CLIs fijados (mismas versiones que el portafolio); el bloqueo de CodeQL lo hace la regla nativa de code scanning del ruleset. | Trivy (una herramienta para todo); un script que parsee el SARIF de CodeQL | Las actions y un release de Trivy fueron comprometidos en marzo de 2026. La regla del ruleset compara el PR con `main`, que es justo "introduce una alerta" (RF-06, RF-07). |
| D5 | Archivo de excepciones y gate propios (`ci/sca_gate.py`) sobre el JSON de Grype. | Reglas `ignore` de `.grype.yaml` | Las reglas de Grype no vencen y borran el hallazgo del reporte; RF-36 pide reportarlo como exceptuado, RF-37 nombrar las vencidas y RF-38 rechazar las incompletas. Un archivo sirve para los dos escaneos. |
| D6 | El bump edita solo la línea de versión de `[project]` en `pyproject.toml` y la del paquete `virtual-assistant` en `uv.lock`, como texto. | Reescribir con un serializador TOML, o regenerar `uv.lock` | RF-19: no hay serializador TOML en la biblioteca estándar (§1) y reescribir puede reordenar o reformatear; regenerar el lock exige ejecutar uv sobre el PR (D1). Cambiar ambas líneas mantiene `uv sync --locked` válido. |
| D7 | La imagen va primero a un `registry:3` local del job, se escanea ahí y se copia a Docker Hub con `imagetools create`. | Publicar en Docker Hub con un tag candidato, o reconstruir desde caché después de escanear | RF-32 prohíbe publicar antes del escaneo; una reconstrucción puede no dar los mismos bytes. Copiar el manifest list conserva el digest escaneado, que `deploy` fija (RF-40). Probado en el release del portafolio. |
| D8 | SBOM en SPDX JSON, uno por plataforma, en un artefacto por versión. | CycloneDX | Ambos cumplen RF-34; SPDX es lo que exporta el dependency graph de GitHub y lo que usa el portafolio. Por plataforma porque las capas de `amd64` y `arm64` difieren (asyncpg y greenlet traen binarios). |
| D9 | Prefijos `build(deps)` y `ci(deps)` literales, con `prefix-development` en `uv`. | `prefix: build` + `include: scope` | `include: scope` da `build(deps-dev)` para el grupo `dev` (pyright, pytest); RF-14 pide `build(deps)` para todo `uv`. |
| D10 | Etiqueta de ecosistema `python:uv` (la que Dependabot ya creó), `github-actions` y `docker`. | `uv` como etiqueta nueva | `python:uv` ya existe y la usan los PRs de la spec 010; cumple RF-15 sin una etiqueta duplicada. |
| D11 | El bump reconstruye la rama con la API de GitHub (mover la ref, `POST /merges` desde `main`, `createCommitOnBranch` con los dos archivos). | Rebase local y push con `--force-with-lease` | Los commits por API los firma GitHub, así que el PR sigue siendo fusionable si el ruleset exige firmas (D14), y los commits de Dependabot quedan intactos. Es la versión que funcionó en el portafolio tras descartar la local. |
| D12 | Imágenes base del `Dockerfile` fijadas como `tag@digest` y uv como etapa `FROM … AS uv`. | Dejar `python:3.14-slim` flotante y `COPY --from=ghcr.io/astral-sh/uv:0.12.23` | Con un tag flotante Dependabot no tiene nada que proponer (RF-10); con digest abre un PR cuando el tag se mueve. Dependabot solo actualiza líneas `FROM`, no `COPY --from=<imagen>`. |
| D13 | Antes del merge se escanea localmente la imagen actual; los hallazgos bloqueantes se corrigen en esta rama (`apt-get upgrade` en la etapa final, o `uv lock --upgrade-package` con aprobación del owner). | Excepciones con vencimiento hasta que salga una imagen base nueva | Si no, el primer release después del merge se detiene (RF-32) y nada se despliega. Las excepciones quedan para falsos positivos, no para deuda conocida. |
| D14 | Al activar el ruleset se mantiene `required_signatures` solo si el owner firma sus commits; hoy `git log --format=%G?` da `U` (sin firma), así que la propuesta es quitar esa regla al activarlo. | Activar el ruleset tal cual | Con `required_signatures` activo, los PRs con commits locales sin firma no se pueden fusionar con merge commit. La spec solo pide checks requeridos (RF-06, RF-28). Decisión del owner. |
| D15 | Los scripts van en `ci/` (paquete con `__init__.py`), con tests en `tests/ci_*_test.py` y en `pyright`. | `scripts/`, como en el portafolio | El `.gitignore` del repo ignora `[Ss]cripts` (plantilla de venv), así que `scripts/` no se versionaría sin tocarlo. `ci/` deja claro que no es código de la API. |

## RF coverage

| RF | Where | Tests |
|---|---|---|
| RF-01 | `codeql.yaml`, `codeql-config.yaml` | E1 |
| RF-02, RF-03, RF-04 | Disparadores de `codeql.yaml` | E1, E13 |
| RF-05 | `codeql.yaml` (`analyze` sube el SARIF) | E1, E13 |
| RF-06, RF-07 | Regla de code scanning del ruleset (D4) | E1, E2 |
| RF-08 | `paths-ignore` de `codeql-config.yaml` | E1 |
| RF-09 | *Default setup* desactivado (Release, paso 2) | E13 |
| RF-10 | `dependabot.yml`, digests del `Dockerfile` (D12) | E5 |
| RF-11, RF-12 | `groups` de `dependabot.yml` | E5 |
| RF-13 | Security updates del repo + `dependabot.yml` | E5 |
| RF-14, RF-15 | `commit-message` y `labels` de `dependabot.yml` (D9, D10) | E5 |
| RF-16 | Ecosistema `github-actions` + comentarios `# vX.Y.Z` | E5 |
| RF-17 | Sin auto-merge configurado | E5 |
| RF-18, RF-19 | `ci/dependabot_bump.py` (`next_patch`, `set_pyproject_version`, `set_lock_version`, `rebump`) | U-DB-1–U-DB-6, U-DB-13, U-DB-14, E5 |
| RF-20 | `bump` en `push` a `main` | U-DB-8, E6 |
| RF-21 | `decide` → `noop` | U-DB-7, E5 |
| RF-22 | `request` en `synchronize` después de recrear | U-DB-9, E7 |
| RF-23 | Condición de `request`, `decide` → `skip` | U-DB-10, E8 |
| RF-24 | Camino de conflicto o push rechazado, status `Version bump` | U-DB-11, U-DB-15–U-DB-17, E9 |
| RF-25 | D1, D2 | E14 (revisión) |
| RF-26, RF-27 | Disparadores de `sca.yaml` | E3, E13 |
| RF-28, RF-29 | `classify` de `ci/sca_gate.py` | U-SG-1–U-SG-4, E3 |
| RF-30 | `summary` de `ci/sca_gate.py`, `upload-sarif` | U-SG-10, E3 |
| RF-31 | Pasos de escaneo de `release.yaml` | E10 |
| RF-32 | Gate antes de `imagetools create` | U-SG-1, E11 |
| RF-33 | Gate que pasa, SARIF y resumen | U-SG-2, E10 |
| RF-34, RF-35 | Pasos de Syft, `upload-artifact` | E10 |
| RF-36 | `validate_exceptions`, `classify` → `excepted` | U-SG-5, U-SG-6, E4 |
| RF-37 | Entradas vencidas | U-SG-7, E4 |
| RF-38 | Entradas inválidas | U-SG-8, U-SG-9, E4 |
| RF-39 | `test.yaml` sin cambios de disparadores | E5, E12 |
| RF-40 | Outputs de `release.yaml`, `deploy` sin cambios | E10 |
| RF-41 | Bloques `permissions` y pins por SHA en todos los workflows | E14 (revisión) |
| RF-42 | D2; ningún `secrets.` en jobs alcanzables desde un `pull_request` de Dependabot | E14 (revisión) |

## Test strategy

**Unit** (`uv run pytest`, sin red; los ejecuta `test.yaml`). Nombres en español como el resto de `tests/`.
`GithubApi` se reemplaza por un fake en memoria con la misma interfaz; las llamadas reales a `gh` las cubren E5–E9.

- `tests/ci_sca_gate_test.py`
  - U-SG-1 — Un match `High` con `fix.state: fixed` es `blocking`; el CLI sale con 1.
  - U-SG-2 — Un match `Critical` con `fix.state: not-fixed` es `reported`; sale con 0.
  - U-SG-3 — Un match `Medium` con corrección es `reported`; sale con 0.
  - U-SG-4 — Un `matches` vacío pasa y el resumen dice que no hay vulnerabilidades conocidas.
  - U-SG-5 — Una excepción válida para el `vulnerability.id` lo deja `excepted`; sale con 0.
  - U-SG-6 — Una excepción para un GHSA cubre el match cuyo `relatedVulnerabilities` lo incluye.
  - U-SG-7 — Una excepción vencida ayer aparece como vencida y el hallazgo es `blocking`; una que vence hoy es válida.
  - U-SG-8 — Una entrada sin `reason` (o vacío) es inválida, se nombra en la salida y sale con 1 aunque no haya hallazgos.
  - U-SG-9 — Una entrada sin `id` o con `expires` que no es fecha ISO (o imposible, `2026-02-30`) es inválida.
  - U-SG-10 — El resumen tiene una fila por hallazgo con paquete, instalada, corregida en, severidad, advisory y estado, y varios `--report` se agrupan bajo su título.
  - U-SG-11 — Un reporte que falta o no es JSON sale con 1 y un error de una línea, sin traza.
- `tests/ci_dependabot_bump_test.py`
  - U-DB-1 — `next_patch("3.9.0")` es `3.9.1`; `next_patch("3.9.9")` es `3.9.10`.
  - U-DB-2 — `next_patch` lanza `ValueError` con `3.9`, `v3.9.0` o una pre-release.
  - U-DB-3 — `set_pyproject_version` sobre el `pyproject.toml` real cambia solo el valor de `version`; el resto es idéntico byte a byte.
  - U-DB-4 — `set_pyproject_version` ignora un `version = ` fuera de `[project]` (por ejemplo en `[tool.*]`).
  - U-DB-5 — `set_lock_version` sobre un fragmento de `uv.lock` cambia solo la versión de `virtual-assistant` y no la de otro paquete con `version` en la línea siguiente.
  - U-DB-6 — `set_lock_version` lanza error si el lock no tiene el paquete `virtual-assistant`.
  - U-DB-7 — `decide` devuelve `noop` cuando la versión del PR es la de `main` + 1 patch.
  - U-DB-8 — `decide` devuelve `rebump` cuando `main` avanzó (PR `3.10.1`, `main` `3.10.1`).
  - U-DB-9 — `decide` devuelve `rebump` cuando el PR tiene la versión de `main` (bump perdido al recrear).
  - U-DB-10 — `decide` devuelve `skip` para cualquier autor que no sea Dependabot.
  - U-DB-11 — `is_bump_commit` reconoce el asunto del bump y nada más (`chore(release): Version 3.9.0` no lo es).
  - U-DB-12 — `trailing_bump` conserva el último commit que no es bump (incluido un merge de `main`), quita solo los bumps finales y rechaza una rama con solo bumps o con un commit después del bump.
  - U-DB-13 — `rebump` sobre una rama con un bump viejo y atrasada respecto de `main`: mueve la rama, hace merge de `main` y agrega un commit de bump con `pyproject.toml` y `uv.lock`, esperando el merge como head.
  - U-DB-14 — `rebump` sobre una rama al día y sin bump solo agrega el commit de bump.
  - U-DB-15 — Un conflicto de merge restaura el head original y no agrega commits.
  - U-DB-16 — Un head que se movió antes o durante la ejecución no recibe escrituras, y la restauración nunca pisa el push de otro.
  - U-DB-17 — Un bump seguido de otro commit nunca se resetea.

**End to end** (en GitHub, durante el release de esta feature; ningún fake emula Dependabot, code scanning ni el registry):

- E1 — PR desechable con SQL armado con texto del request (`text(f"… {param}")` en un router): CodeQL reporta una alerta alta y el PR queda bloqueado; al quitarlo se desbloquea. No aparecen hallazgos de `.venv`.
- E2 — PR desechable con una alerta solo media: los checks pasan y la alerta se ve en Security.
- E3 — PR desechable que fija una dependencia en una versión con un advisory alto corregido: `Lockfile Scan` falla con la tabla en el resumen y la alerta en Security.
- E4 — El mismo PR con una excepción válida: pasa y la lista como exceptuada; con `expires` pasado: falla nombrando la entrada; sin `reason`: falla nombrando la entrada.
- E5 — "Check for updates" de Dependabot: un PR agrupado menor/patch por ecosistema con el prefijo y las etiquetas correctas, mayores aparte, Actions fijadas por SHA; cada PR recibe exactamente un commit de bump a `main` + 1 patch en `pyproject.toml` y `uv.lock`, con el badge `Verified`, y su head nuevo corre los checks en verde (incluido `uv sync --locked` de `test.yaml`); una segunda ejecución no agrega commits.
- E6 — Fusionar un PR de Dependabot: los otros abiertos se re-bumpean a la nueva versión en minutos.
- E7 — `@dependabot recreate`: el commit de bump vuelve.
- E8 — Un PR de una rama `feature/*` nunca es tocado por el workflow de bump.
- E9 — Forzar un conflicto (cambiar en `main` la misma línea de `uv.lock` que el PR): sin push, status `Version bump` fallido y un comentario.
- E10 — Fusionar esta feature: el release escanea la imagen en las dos plataformas, la publica con el mismo digest que fija el deploy, `rollout status` pasa y existe el artefacto `virtual-assistant-3.10.0-sbom` con dos SBOM.
- E11 — Ejecutar Grype (`docker run anchore/grype`) y `ci/sca_gate.py` localmente contra una imagen `python` vieja con altos corregidos: sale con 1; revisar en el YAML que `imagetools create`, el único push a Docker Hub, va después del gate. (Forzar un bloqueante en el release real implicaría desplegar a propósito.)
- E12 — `uv run pytest` y `uv run pyright` en verde localmente y en `test.yaml`; `uv run alembic check` sin diferencias.
- E13 — Al día siguiente: ejecuciones programadas de CodeQL y SCA en Actions; ninguna ejecución de "default setup".
- E14 — Revisión: cada workflow tiene `permissions`, cada action de terceros está fijada por SHA y ningún `secrets.` aparece en un job alcanzable desde un `pull_request` de Dependabot.

## Implementation order

1. Pre-escaneo local de la imagen actual y de `uv.lock` con Grype en Docker (D13); verificar que Grype lee `uv.lock`. Corregir lo bloqueante si hace falta, con aprobación del owner si cambia `uv.lock`.
2. `ci/__init__.py`, `ci/sca_gate.py` y sus tests; `.github/sca-exceptions.json`; `pyproject.toml` (`include` de pyright); `.dockerignore`.
3. `ci/dependabot_bump.py` y sus tests.
4. `codeql.yaml` y `codeql-config.yaml`.
5. `sca.yaml`.
6. `dependabot-bump.yaml`.
7. `release.yaml`: registry local, escaneo, gate, SARIF, copia a Docker Hub, SBOM; comentarios de versión en `test.yaml` y `release.yaml`.
8. `Dockerfile` con digests y etapa `uv`; `dependabot.yml`.
9. `README.md` y `SECURITY.md`.
10. Checklist de cierre y `chore(release): Bump version to v3.10.0` con `uv sync`.

## Release

1. Instalar la App `portfolio-version-bump` en el repo; crear `BUMP_APP_ID` y `BUMP_APP_PRIVATE_KEY`; crear las etiquetas `github-actions` y `docker`.
2. Al abrir el PR, desactivar el *default setup* de CodeQL (GitHub rechaza las subidas del setup avanzado mientras está activo).
3. Comprobar que `CodeQL`, `SCA` y `Backend Tests` pasan en el PR (E1–E4 en ramas desechables que salen de él).
4. Actualizar y activar el ruleset de `main` con los checks requeridos y la regla de code scanning, con los nombres de check que muestra el PR (D14).
5. Fusionar y vigilar el release (E10). Si fallan los pasos nuevos, revertir el merge: el `release.yaml` anterior publica como antes.
6. Disparar Dependabot desde el dependency graph (E5–E9); al día siguiente, E13.
7. `/tag-release` 3.10.0.

## Risks

- **Grype y `uv.lock`:** si el catalogador de Syft no reconoce `uv.lock` como archivo suelto, `Lockfile Scan` no encuentra nada y pasa en falso. Mitigación: el paso 1 lo comprueba; si falla, escanear `dir:.` limitado al lock, u `osv-scanner` (que soporta `uv.lock`) como alternativa a decidir.
- **Imagen base Debian con hallazgos corregidos:** `python:3.14-slim` puede traer paquetes con altos ya corregidos en Debian que bloqueen el primer release. Mitigación: D13 y `apt-get upgrade` en la etapa final.
- **Versión de uv desalineada:** Dependabot actualiza la etapa `uv` del `Dockerfile`, pero no el `version: 0.12.23` de `setup-uv` en los workflows. Impacto: CI y la imagen podrían usar versiones distintas de uv. Mitigación: el README lo anota y el owner alinea el workflow al revisar ese PR.
- **`uv sync --locked` tras el bump:** si uv guardara en `uv.lock` algo más que la versión del proyecto (por ejemplo, un hash), la edición de texto (D6) dejaría un lock inválido. Mitigación: U-DB-5 usa el lock real y E5 lo comprueba con `test.yaml`; si falla, el status `Version bump` lo deja visible (RF-24).
- **Dispatch tratado como ejecución de Dependabot:** si GitHub no entrega secretos a la ejecución despachada (D2), el bump solo llega con el horario. Mitigación: el horario (D3); en el portafolio el dispatch funciona.
- **`imagetools create` desde un registry HTTP:** funciona en el portafolio con la misma configuración de Buildx; si no, `crane copy` (descargado y fijado) conserva el digest.
- **Falla la descarga de la base de Grype:** el escaneo falla y el release se detiene (falla cerrado). Mitigación: `cache-db: true` y reintentar el job.
- **Tiempo del release:** dos escaneos y dos SBOM suman unos 2–4 minutos a `package`.
