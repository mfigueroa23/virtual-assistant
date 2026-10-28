# Tasks 012 — DevSecOps: CodeQL, Dependabot y SCA

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea empieza solo cuando están hechas las de "Depende de". Las tareas con las mismas
  dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.

## Phase 0 — Gate

- [ ] **T01 — Decidir la firma de commits del ruleset**
  - **RF:** RF-06, RF-28
  - **Depende de:** — (en paralelo con T02 y T03)
  - **Alcance:** el owner elige, según D14, entre quitar `required_signatures` del `Branch Ruleset` al activarlo o
    configurar la firma de sus commits locales y dejarla. La elección se anota en D14 del plan.
  - **Hecho cuando:** D14 dice qué se hace con `required_signatures` y, si se mantiene, `git log -1 --format=%G?` de un
    commit nuevo del owner da `G`.

- [x] **T02 — Pre-escaneo local de `uv.lock` y de la imagen**
  - **RF:** RF-26, RF-31
  - **Depende de:** — (en paralelo con T01 y T03)
  - **Alcance:** con `docker run anchore/grype:v0.120.1` (sin instalar nada en el proyecto), escanear `file:uv.lock` y la
    imagen construida con `docker build` para `linux/amd64` y `linux/arm64`, en JSON. Comprobar el riesgo "Grype y
    `uv.lock`" del plan y listar los hallazgos críticos/altos con corrección (D13). Si hay bloqueantes en `uv.lock`,
    pedir al owner la aprobación de `uv lock --upgrade-package`; si Grype no lee `uv.lock`, decidir la alternativa
    (riesgo del plan) antes de T08.
  - **Hecho cuando:** `grype file:uv.lock -o json` lista paquetes Python de `uv.lock` (`.descriptor` y
    `.source.target` del reporte apuntan a `uv.lock` y Syft cataloga más de 0 paquetes), y la lista de bloqueantes (o
    "ninguno") con la corrección elegida para cada uno queda anotada en D13 del plan.

- [x] **T03 — Preparar la App y las etiquetas (owner)**
  - **RF:** RF-15, RF-18
  - **Depende de:** — (en paralelo con T01 y T02)
  - **Alcance:** instalar `portfolio-version-bump` en `mfigueroa23/virtual-assistant`; crear la variable `BUMP_APP_ID`
    (`5463156`) y el secreto de Actions `BUMP_APP_PRIVATE_KEY` (D2, D3); crear las etiquetas `github-actions` y
    `docker` (D10).
  - **Hecho cuando:** `gh variable list` muestra `BUMP_APP_ID`, `gh secret list` muestra `BUMP_APP_PRIVATE_KEY` y
    `gh label list` muestra `dependencies`, `python:uv`, `github-actions` y `docker`.

## Phase 1 — Cimientos

- [x] **T04 — Gate del SCA y archivo de excepciones**
  - **RF:** RF-28, RF-29, RF-30, RF-33, RF-36, RF-37, RF-38
  - **Depende de:** — (en paralelo con T01–T03)
  - **Alcance:** `ci/__init__.py`; `ci/sca_gate.py` (`validate_exceptions`, `classify`, `findings`, `summary`, CLI con
    `--report`, `--title`, `--exceptions`, `SCA_GATE_TODAY`, textos en español; D5, D15); `.github/sca-exceptions.json`
    con `[]`; `tests/ci_sca_gate_test.py`; `"ci"` en `[tool.pyright].include` de `pyproject.toml`; `ci` en
    `.dockerignore`.
  - **Hecho cuando:** `uv run pytest tests/ci_sca_gate_test.py` pasa con U-SG-1–U-SG-11 y `uv run pyright` da 0 errores.

- [x] **T05 — Script del bump de versión**
  - **RF:** RF-18, RF-19, RF-20, RF-21, RF-22, RF-23, RF-24, RF-25
  - **Depende de:** T04
  - **Alcance:** `ci/dependabot_bump.py` (`next_patch`, `project_version`, `set_pyproject_version`,
    `set_lock_version`, `is_bump_commit`, `decide`, `trailing_bump`, `rebump`, `GithubApi` con `gh` vía
    `subprocess.run` sin shell, CLI con `--pr`, status `Version bump` y comentario en español; D1, D6, D11);
    `tests/ci_dependabot_bump_test.py` con un fake en memoria de `GithubApi`. U-DB-3 y U-DB-5 usan el `pyproject.toml`
    y un fragmento del `uv.lock` reales.
  - **Hecho cuando:** `uv run pytest tests/ci_dependabot_bump_test.py` pasa con U-DB-1–U-DB-17, `uv run pyright` da 0
    errores y `grep -nE "shell=True|\"git\"|\"uv\"" ci/dependabot_bump.py` no encuentra nada.

- [x] **T06 — `Dockerfile` con digests y etapa de uv**
  - **RF:** RF-10, RF-31, RF-32
  - **Depende de:** T02, T04
  - **Alcance:** fijar `python:3.14-slim@sha256:…` en las dos etapas y mover uv a `FROM ghcr.io/astral-sh/uv:0.12.23@sha256:… AS uv`
    con `COPY --from=uv /uv /bin/uv` (D12). Aplicar las correcciones anotadas en D13 por T02 (`apt-get upgrade` en la
    etapa final y/o `uv lock --upgrade-package` aprobado).
  - **Hecho cuando:** `docker build .` termina sin errores; el contenedor responde en `/health` con la base de datos
    local; y Grype sobre la imagen nueva (`linux/amd64` y `linux/arm64`) pasado por `python3 ci/sca_gate.py` sale con 0.

## Phase 2 — Workflows

- [x] **T07 — CodeQL avanzado**
  - **RF:** RF-01, RF-02, RF-03, RF-04, RF-05, RF-08, RF-41
  - **Depende de:** — (en paralelo con T04–T06)
  - **Alcance:** `.github/workflows/codeql.yaml` (matriz `actions` y `python`, `build-mode: none`, disparadores PR,
    push, diario y manual, permisos mínimos, pins con `# vX.Y.Z`; D1, D4) y `.github/codeql/codeql-config.yaml`
    (`security-extended`, `paths-ignore` de `.venv` y `**/__pycache__`).
  - **Hecho cuando:** actionlint (`docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.7`, sin instalarlo en
    el proyecto) no reporta errores en `codeql.yaml` y cada `uses:` lleva un SHA de 40 caracteres.

- [x] **T08 — Workflow del SCA de `uv.lock`**
  - **RF:** RF-26, RF-27, RF-28, RF-29, RF-30, RF-41
  - **Depende de:** T02, T04
  - **Alcance:** `.github/workflows/sca.yaml` con el job `Lockfile Scan` (Grype `file:uv.lock` o la alternativa
    decidida en T02, `python3 ci/sca_gate.py`, `upload-sarif` con `category: sca-lockfile` salvo PRs de forks; D4, D5).
  - **Hecho cuando:** actionlint no reporta errores en `sca.yaml`, cada `uses:` lleva un SHA de 40 caracteres y el
    workflow no referencia `secrets.`.

- [x] **T09 — Workflow del bump de Dependabot**
  - **RF:** RF-18, RF-20, RF-22, RF-23, RF-24, RF-25, RF-41, RF-42
  - **Depende de:** T05
  - **Alcance:** `.github/workflows/dependabot-bump.yaml` con el job `request` (solo PRs de `dependabot[bot]`, solo
    `actions: write`, sin checkout, despacha en `main`) y el job `bump` (dispatch, `push` a `main` con
    `paths: pyproject.toml`, horario, token de la App, `python3 ci/dependabot_bump.py`, `concurrency` sin cancelar;
    D2, D3).
  - **Hecho cuando:** actionlint no reporta errores; el job `request` no tiene `secrets.` ni `actions/checkout`; el job
    `bump` tiene `if` que excluye `pull_request` y solo corre en `refs/heads/main`.

- [x] **T10 — Release con escaneo, gate y SBOM**
  - **RF:** RF-16, RF-31, RF-32, RF-33, RF-34, RF-35, RF-39, RF-40, RF-41, RF-42
  - **Depende de:** T04, T06
  - **Alcance:** en `.github/workflows/release.yaml`, el job `package` pasa a `Build, Scan and Publish Image` con el
    registry local, Buildx en red del host, build a `localhost:5000`, escaneo por plataforma, `ci/sca_gate.py`, SARIF
    por plataforma, `imagetools create` a Docker Hub con verificación de digest y SBOM SPDX por plataforma como
    artefacto `virtual-assistant-<version>-sbom` (D7, D8). `verify` y `deploy` no cambian sus pasos. Comentarios
    `# vX.Y.Z` en todos los pins de `release.yaml` y `test.yaml` (sin otros cambios en `test.yaml`).
  - **Hecho cuando:** actionlint no reporta errores en los dos workflows; en `release.yaml` el único paso que publica en
    Docker Hub (`imagetools create`) va después de `Check Findings`; el output `digest` sigue saliendo de
    `steps.build.outputs.digest`; `git diff main -- .github/workflows/test.yaml` solo muestra comentarios.

- [x] **T11 — Configuración de Dependabot**
  - **RF:** RF-10, RF-11, RF-12, RF-13, RF-14, RF-15, RF-16, RF-17
  - **Depende de:** T06
  - **Alcance:** reescribir `.github/dependabot.yml` con `uv`, `github-actions` y `docker` semanales, grupos
    menor/patch con `applies-to: version-updates`, prefijos literales y etiquetas (D9, D10), sin
    `open-pull-requests-limit: 0` ni auto-merge.
  - **Hecho cuando:** el archivo tiene las tres entradas con `groups`, `commit-message` y `labels`, y
    `grep -n "open-pull-requests-limit\|auto-merge" .github/dependabot.yml` no encuentra nada. (GitHub lo valida tras el
    push: lo comprueba T18.)

## Phase 3 — Documentación, calidad y versión

- [x] **T12 — README y SECURITY.md**
  - **RF:** —
  - **Depende de:** T07, T08, T09, T10, T11
  - **Alcance:** en `README.md`, sección **CI/CD**: workflows nuevos, la App y sus variables, el párrafo de Dependabot
    reescrito, el archivo de excepciones y cómo agregar una, la nota de alinear `setup-uv` con la etapa `uv` del
    `Dockerfile`, y el nombre real del deployment (`deployment/virtual-assistant`, namespace `virtual-assistant`). En
    `SECURITY.md`, la sección "Análisis de seguridad en CI".
  - **Hecho cuando:** la tabla de CI/CD del README lista `codeql.yaml`, `sca.yaml` y `dependabot-bump.yaml`;
    `grep -n "open-pull-requests-limit\|chatbot-backend" README.md` no encuentra nada; `SECURITY.md` explica qué
    bloquea y cómo pedir una excepción.

- [x] **T13 — Puerta de calidad y revisión de seguridad**
  - **RF:** RF-25, RF-39, RF-41, RF-42
  - **Depende de:** T12
  - **Alcance:** checklist de `AGENTS.md` y revisión de los workflows.
  - **Hecho cuando:** E12 pasa (`uv run pytest` y `uv run pyright` en verde, `uv run alembic check` sin diferencias);
    E14 se cumple (cada workflow tiene `permissions`, cada action de terceros va por SHA y ningún job alcanzable desde
    un `pull_request` de Dependabot usa `secrets.`); y E11 pasa (Grype local sobre una imagen `python` vieja con altos
    corregidos, por `ci/sca_gate.py`, sale con 1).

- [x] **T14 — Subir la versión a 3.10.0**
  - **RF:** —
  - **Depende de:** T13
  - **Alcance:** `version = "3.10.0"` en `pyproject.toml` y `uv sync`, en un solo commit
    `chore(release): Bump version to v3.10.0` con `uv.lock`.
  - **Hecho cuando:** `git show --stat HEAD` solo lista `pyproject.toml` y `uv.lock`, y `uv sync --locked` pasa.

## Phase 4 — Release y pruebas de punta a punta

- [ ] **T15 — PR, setup de CodeQL y pruebas de bloqueo**
  - **RF:** RF-02, RF-05, RF-06, RF-07, RF-09, RF-26, RF-28, RF-29, RF-30, RF-36, RF-37, RF-38
  - **Depende de:** T14
  - **Alcance:** abrir el PR (`/create-pr`); desactivar el *default setup* de CodeQL (Release, paso 2); comprobar que
    `CodeQL`, `SCA` y `Backend Tests` pasan; ramas desechables desde la del PR para E1–E4. Las pruebas de bloqueo
    dependen de la regla del ruleset: E1 y E2 se confirman después de T16.
  - **Hecho cuando:** `gh api …/code-scanning/default-setup --jq .state` da `not-configured`; los checks del PR están
    en verde; E3 y E4 se cumplen; E1 muestra la alerta alta en Security.

- [ ] **T16 — Activar el ruleset de `main`**
  - **RF:** RF-06, RF-07, RF-28
  - **Depende de:** T01, T15
  - **Alcance:** el `Branch Ruleset` suma `Lockfile Scan`, `Analyze (actions)` y `Analyze (python)` (con los nombres que
    muestra el PR) y la regla de code scanning de CodeQL (`high_or_higher`, `none`); `required_signatures` según T01; se
    activa (D4, D14).
  - **Hecho cuando:** `gh api repos/mfigueroa23/virtual-assistant/rulesets/24680483 --jq .enforcement` da `active` y
    las reglas incluyen los cuatro checks y `code_scanning`; E1 (PR con la alerta alta bloqueado, desbloqueado al
    quitarla) y E2 (alerta media no bloquea) se cumplen.

- [ ] **T17 — Fusionar y vigilar el release**
  - **RF:** RF-03, RF-31, RF-33, RF-34, RF-35, RF-40
  - **Depende de:** T16
  - **Alcance:** fusionar el PR y seguir `release.yaml` (Release, paso 5). Si fallan los pasos nuevos, revertir el merge.
  - **Hecho cuando:** E10 se cumple: el release escanea las dos plataformas, `docker buildx imagetools inspect` del tag
    `3.10.0` da el mismo digest que fijó el deploy, `rollout status` pasa y existe el artefacto
    `virtual-assistant-3.10.0-sbom` con dos SBOM.

- [ ] **T18 — Dependabot y bump de punta a punta**
  - **RF:** RF-10, RF-11, RF-12, RF-13, RF-14, RF-15, RF-16, RF-17, RF-18, RF-19, RF-20, RF-21, RF-22, RF-23, RF-24, RF-39, RF-42
  - **Depende de:** T03, T17
  - **Alcance:** disparar "Check for updates" en el dependency graph para los tres ecosistemas y recorrer E5–E9.
  - **Hecho cuando:** E5 (PRs agrupados con prefijo, etiquetas y un único bump `Verified` en `pyproject.toml` y
    `uv.lock`, checks en verde), E6 (re-bump tras fusionar uno), E7 (`@dependabot recreate`), E8 (PR `feature/*`
    intacto) y E9 (conflicto con status fallido y comentario) se cumplen.

- [ ] **T19 — Ejecuciones programadas y tag**
  - **RF:** RF-04, RF-09, RF-27
  - **Depende de:** T17
  - **Alcance:** al día siguiente del merge, revisar Actions y Security; publicar el release con `/tag-release` 3.10.0.
  - **Hecho cuando:** E13 se cumple (ejecuciones programadas de `CodeQL` y `SCA` en Actions, ninguna de "default
    setup") y `gh release view 3.10.0` existe.

## RF → tasks

| RF | Tasks |
|---|---|
| RF-01 | T07 |
| RF-02 | T07, T15 |
| RF-03 | T07, T17 |
| RF-04 | T07, T19 |
| RF-05 | T07, T15 |
| RF-06, RF-07 | T01, T15, T16 |
| RF-08 | T07 |
| RF-09 | T15, T19 |
| RF-10 | T06, T11, T18 |
| RF-11, RF-12, RF-13, RF-14 | T11, T18 |
| RF-15 | T03, T11, T18 |
| RF-16 | T10, T11, T18 |
| RF-17 | T11, T18 |
| RF-18 | T03, T05, T09, T18 |
| RF-19, RF-21 | T05, T18 |
| RF-20, RF-22, RF-23, RF-24 | T05, T09, T18 |
| RF-25 | T05, T09, T13 |
| RF-26 | T02, T08, T15 |
| RF-27 | T08, T19 |
| RF-28 | T01, T04, T08, T15, T16 |
| RF-29, RF-30 | T04, T08, T15 |
| RF-31 | T02, T06, T10, T17 |
| RF-32 | T06, T10 |
| RF-33 | T04, T10, T17 |
| RF-34, RF-35 | T10, T17 |
| RF-36, RF-37, RF-38 | T04, T15 |
| RF-39 | T10, T13, T18 |
| RF-40 | T10, T17 |
| RF-41 | T07, T08, T09, T10, T13 |
| RF-42 | T09, T10, T13, T18 |
