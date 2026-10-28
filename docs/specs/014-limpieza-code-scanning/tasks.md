# Tasks 014 — Limpieza de las alertas de code scanning

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea empieza solo cuando están hechas las de "Depende de". Las tareas con las mismas
  dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.

## Phase 0 — Gate

- [x] **T01 — Aprobaciones del owner**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** las cuatro aprobaciones del plan:
    - la constitución §1 y `AGENTS.md` pasan a Python 3.15 (D8);
    - `uv.lock` se regenera en una tarea intermedia (D4);
    - el `Dockerfile` usa `python:3.15-alpine` con `build-base` solo en la etapa `build` (D5, D6), y `setup-uv` pasa a
      0.13.6 en `test.yaml` y `release.yaml` (D7);
    - se corre `anchore/grype` en contenedor antes del PR (D9).
  - **Hecho cuando:** el owner aprueba el plan en la conversación del 2026-10-28.

## Phase 1 — Barrera de CodeQL

- [x] **T02 — Barrera `re.fullmatch` en `create_message` y `download`**
  - **RF:** RF-01, RF-02, RF-03, RF-04
  - **Depende de:** T01 (en paralelo con T03)
  - **Alcance:** en `src/services/google_chat.py`:
    - `create_message` valida con `if not re.fullmatch(SPACE_NAME, space): raise InvalidChatNameError("space")`, en la
      misma función que arma la URL (D1);
    - `download` hace lo mismo con `ATTACHMENT_NAME` (D2);
    - `valid_space()`, `valid_attachment_name()` y quienes las llaman no cambian, y un comentario cita D1.

    Agregar U-GC-6 y U-GC-7 a `tests/google_chat_test.py` con `mock_http`.
  - **Hecho cuando:**
    - `uv run pytest tests/google_chat_test.py tests/google_chat_reply_test.py tests/whatsapp_handoff_test.py tests/faq_learning_test.py`
      pasa con U-GC-1 a U-GC-7, U-GR-1 a U-GR-3, U-WH-1, U-WH-2 y U-FL-1;
    - `uv run pyright` termina con 0 errores.

## Phase 2 — Runtime 3.15

- [x] **T03 — Python 3.15 en el proyecto, `uv.lock` y la documentación del stack**
  - **RF:** RF-05, RF-07, RF-09
  - **Depende de:** T01 (en paralelo con T02)
  - **Alcance:**
    - `.python-version` a `3.15` y `requires-python = ">=3.15"` en `pyproject.toml` (D3);
    - `uv lock --upgrade-package ormsgpack --upgrade-package uuid-utils --upgrade-package websockets` (D4), sin tocar
      `version`;
    - `rm -rf .venv && uv sync`;
    - en el mismo commit (D8): la constitución §1 y `AGENTS.md` dicen Python 3.15, y el README actualiza las líneas 23
      y 91 y reemplaza la nota de la línea 27 por una sobre `pyyaml`, `zstandard` y `websockets`, que se compilan
      desde el código fuente con un compilador de C.
  - **Hecho cuando:**
    - `uv run python --version` responde `3.15.x`;
    - en `git diff uv.lock`, los únicos cambios de versión son `ormsgpack` 1.13.0 y `uuid-utils` 0.19.0, además de
      `requires-python` y las ruedas cp314 que salen;
    - `uv run pytest` pasa con al menos 475 tests y `uv run pyright` termina con 0 errores;
    - `grep -n "3\.14" AGENTS.md docs/constitution.md` no devuelve nada.

- [x] **T04 — uv 0.13.6 en el CI**
  - **RF:** RF-05
  - **Depende de:** T03 (en paralelo con T05)
  - **Alcance:** `astral-sh/setup-uv` con `version: 0.13.6` en `.github/workflows/test.yaml` y `release.yaml` (D7). No
    cambian ni el SHA del action ni otros pasos.
  - **Hecho cuando:**
    - `grep -n "version: 0\." .github/workflows/test.yaml .github/workflows/release.yaml` muestra `0.13.6` en los dos;
    - `uvx uv@0.13.6 python list 3.15 --only-downloads --all-platforms --all-arches` lista `cpython-3.15.2-linux-x86_64-gnu`.

## Phase 3 — Imagen

- [ ] **T05 — `Dockerfile` sobre `python:3.15-alpine`**
  - **RF:** RF-05, RF-06, RF-07, RF-08, RF-10, RF-11, RF-12, RF-13, RF-14
  - **Depende de:** T03 (en paralelo con T04)
  - **Alcance:**
    - en `Dockerfile`:
      - las dos etapas pasan a `python:3.15-alpine@sha256:…`, con el digest del índice multi-plataforma vigente (D5);
      - la etapa `build` agrega `apk add --no-cache build-base` antes de `uv sync --locked` (D6);
      - la final usa `/usr/local/lib/python3.15/lib-dynload`, y el resto no cambia;
      - los comentarios citan la spec 014 donde cambia algo;
    - `README.md` (línea 284): la base es `python:3.15-alpine` y el PR que se cierra sin fusionar es el de
      `3.16-alpine`;
    - `SECURITY.md:45`: la imagen es `python:3.15-alpine`.

    Verificar con `docker buildx` en las dos plataformas y un PostgreSQL local en contenedor.
  - **Hecho cuando:** se cumplen E1 y E2 en `linux/amd64` y `linux/arm64`:
    - `python --version` responde 3.15.x, se aplican las migraciones y `/health` responde `200`;
    - el healthcheck queda `healthy` y el uid es distinto de 0;
    - `gcc` y `apk` no están en la imagen final, y `/lib/apk/db/installed` no lista `apk-tools`, `ncurses-libs`,
      `readline` ni `build-base`;
    - `import yaml, zstandard, websockets, asyncpg, greenlet, uvloop` funciona;
    - con `DB_HOST` inalcanzable, el contenedor termina con un código distinto de 0.

- [ ] **T06 — Pre-escaneo de la imagen 3.15 con Grype**
  - **RF:** RF-15, RF-16
  - **Depende de:** T05
  - **Alcance:** `docker run anchore/grype:v0.120.1` en JSON sobre las dos plataformas de la imagen de T05, sin
    instalar nada en el proyecto (D9). Si aparece un hallazgo con fix, se corrige en la rama (digest más reciente o
    paquete) y se repiten T05 y este escaneo. Si no hay corrección aplicable, se detiene la spec y se vuelve al owner
    (decisión "Si la migración resulta inviable" de la spec).
  - **Hecho cuando:** se cumple E3: 0 hallazgos con fix en `amd64` y `arm64`, y no aparecen CVE-2025-15367 ni
    CVE-2026-12345.

## Phase 4 — Cierre

- [ ] **T07 — Puerta de calidad y subida de versión**
  - **RF:** RF-09, RF-19
  - **Depende de:** T02, T03, T04, T05, T06
  - **Alcance:** checklist de `AGENTS.md`:
    - `uv run pytest`, `uv run pyright` y `uv run alembic check`;
    - `version = "3.11.0"` en `pyproject.toml` y `uv sync`;
    - un solo commit `chore(release): Bump version to v3.11.0` con `pyproject.toml` y `uv.lock`.
  - **Hecho cuando:**
    - `uv run pytest` pasa completo, incluido `tests/ci_sca_gate_test.py`;
    - `uv run pyright` termina con 0 errores y `uv run alembic check` no detecta cambios;
    - `git show --stat HEAD` solo muestra `pyproject.toml` y `uv.lock`.

- [ ] **T08 — PR y verificación en CI**
  - **RF:** RF-01, RF-05, RF-17, RF-18, RF-19
  - **Depende de:** T07
  - **Alcance:** abrir el PR desde `fix/code-scanning-python-315` con `/create-pr`, y revisar E4. Si `Analyze (python)`
    sigue reportando `py/partial-ssrf` en `google_chat.py`, aplicar D10 (barrera en forma positiva), volver a correr los
    tests de T02 y subir el commit. Si tampoco basta, volver al owner (la spec no permite descartar la alerta).
  - **Hecho cuando:** se cumple E4:
    - `Analyze (python)` no reporta `py/partial-ssrf`;
    - el log de `Type Check & Tests` muestra Python 3.15.2;
    - `Lockfile Scan` no trae hallazgos con fix;
    - todos los checks del ruleset están en verde.

- [ ] **T09 — Release y comprobación final**
  - **RF:** RF-01, RF-03, RF-06, RF-14, RF-15, RF-16
  - **Depende de:** T08 (con el merge `chore(release): Version 3.11.0` en `main`)
  - **Alcance:** seguir el release de `release.yaml`, esperar al CodeQL de `main`, enviar el mensaje de prueba en Google
    Chat y revisar Dependabot. Después, `/tag-release`.
  - **Hecho cuando:**
    - E5: el release escanea, sube el SBOM, publica, despliega por digest y pasa `rollout status`, y luego Security
      tiene 0 alertas abiertas, con #4, #34, #44, #196 y #206 como *fixed*;
    - E6: el mensaje de prueba recibe la respuesta diferida en su hilo;
    - E7: *Dependency graph → Dependabot* lista `python:3.15-alpine` sin errores.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T02, T08, T09 |
| RF-02 | T02 |
| RF-03 | T02, T09 |
| RF-04 | T02 |
| RF-05 | T03, T04, T05, T08 |
| RF-06 | T05, T09 |
| RF-07 | T03, T05 |
| RF-08 | T05 |
| RF-09 | T03, T07 |
| RF-10, RF-11, RF-12, RF-13 | T05 |
| RF-14 | T05, T09 |
| RF-15, RF-16 | T06, T09 |
| RF-17, RF-18 | T08 |
| RF-19 | T07, T08 |
