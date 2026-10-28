# Tasks 013 — Remediación de los hallazgos de code scanning

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea empieza solo cuando están hechas las de "Depende de". Las tareas con las mismas
  dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `E#` = tests de la estrategia de tests del plan.

## Phase 1 — Cimientos

- [x] **T01 — Validación de nombres de Google Chat en el sink**
  - **RF:** RF-01, RF-02, RF-06, RF-07
  - **Depende de:** — (en paralelo con T05, T06 y T08)
  - **Alcance:** `InvalidChatNameError(kind)` en `src/utils/exceptions/google_chat.py`, con un mensaje sin el valor
    recibido. `SPACE_NAME`, `ATTACHMENT_NAME`, `valid_space()` y `valid_attachment_name()` en
    `src/services/google_chat.py`. `create_message` y `download` lanzan la excepción antes de cualquier request (D8, D9).
    Tests en `tests/google_chat_test.py` con `httpx.MockTransport`.
  - **Hecho cuando:** `uv run pytest tests/google_chat_test.py` pasa con U-GC-1 a U-GC-5 y con los tests actuales del
    archivo.

## Phase 2 — Integración con los flujos existentes

- [x] **T02 — Space inválido en la respuesta diferida y adjuntos inválidos**
  - **RF:** RF-03, RF-07
  - **Depende de:** T01 (en paralelo con T03 y T04)
  - **Alcance:** `deliver` en `src/services/google_chat_reply.py` trata un space inválido como el que falta:
    `logger.error` sin el valor y salida antes de `chat_token`. `read_files` no cambia; el error de `download` lo
    absorbe `read_one` (D8, D9). Tests en `tests/google_chat_reply_test.py`.
  - **Hecho cuando:** `uv run pytest tests/google_chat_reply_test.py tests/attachments_test.py` pasa con U-GR-1, U-GR-2
    y U-GR-3. En U-GR-1, `caplog` no contiene el space rechazado.

- [x] **T03 — Property `whatsapp_notify_space` inválida**
  - **RF:** RF-04
  - **Depende de:** T01 (en paralelo con T02 y T04)
  - **Alcance:** `publish_notice` en `src/services/whatsapp_handoff.py` valida la property justo después de leerla. Si
    no es válida: un `logger.error` sin el valor, `None`, y no se piden token ni reintentos (D9). Tests en
    `tests/whatsapp_handoff_test.py` con `FakeChat` y `fake_properties`.
  - **Hecho cuando:** `uv run pytest tests/whatsapp_handoff_test.py` pasa con U-WH-1 y U-WH-2. En U-WH-1, `FakeChat`
    registra 0 llamadas a `chat_token` y `create_message`.

- [x] **T04 — Hilo con space inválido al publicar una propuesta**
  - **RF:** RF-05
  - **Depende de:** T01 (en paralelo con T02 y T03)
  - **Alcance:** `publish_proposal` en `src/services/faq_learning.py` valida el space que saca del hilo. Si no es
    válido: un `logger.error` sin el hilo y sale sin publicar (D9). Test en `tests/faq_learning_test.py`.
  - **Hecho cuando:** `uv run pytest tests/faq_learning_test.py` pasa con U-FL-1 y con los tests actuales.

- [x] **T05 — Id de propuesta limpio en los logs de las tarjetas**
  - **RF:** RF-08, RF-09
  - **Depende de:** — (en paralelo con T01, T06 y T08)
  - **Alcance:** función privada `loggable()` en `src/services/knowledge_card.py`. Quita los caracteres no imprimibles
    y además `\r`/`\n` con `replace`, y se aplica a `proposal_id` en los dos `logger.error` de `handle_click`. La
    búsqueda de la propuesta no cambia (D10). Tests en `tests/knowledge_card_test.py`.
  - **Hecho cuando:** `uv run pytest tests/knowledge_card_test.py` pasa con U-KC-1, U-KC-2 y con los tests actuales de
    aplicar y descartar.

## Phase 3 — Imagen

- [x] **T06 — `Dockerfile` sobre `python:3.14-alpine`**
  - **RF:** RF-10, RF-13, RF-14, RF-15, RF-16, RF-17
  - **Depende de:** — (en paralelo con T01, T05 y T08)
  - **Alcance:** en `Dockerfile`:
    - las etapas `build` y final pasan a `python:3.14-alpine@sha256:…` (tag@digest, D1, D2);
    - la etapa final quita `_curses*` y `readline` de `lib-dynload`, recalcula con `scanelf` las librerías de runtime y
      borra `apk-tools` al final (D3);
    - el usuario se crea con `adduser -S -D -H app` (D4);
    - `entrypoint.sh`, `HEALTHCHECK`, `ENTRYPOINT` y `CMD` no cambian (D5).

    Verificar con `docker buildx` para las dos plataformas y un PostgreSQL local en contenedor.
  - **Hecho cuando:** se cumplen E1 y E2 en `linux/amd64` y `linux/arm64`:
    - migraciones aplicadas y `/health` en `200`;
    - healthcheck `healthy`;
    - uid distinto de 0;
    - `/lib/apk/db/installed` sin `apk-tools`, `ncurses-libs`, `readline`, `perl`, `util-linux` ni `shadow`;
    - con `DB_HOST` inalcanzable, el contenedor sale con código distinto de 0.

- [x] **T07 — Pre-escaneo de la imagen Alpine**
  - **RF:** RF-10, RF-11
  - **Depende de:** T06
  - **Alcance:** antes de correrlo, pedir al owner que apruebe ejecutar `docker run anchore/grype:v0.120.1` (sin
    instalar nada en el proyecto, como T02 de la spec 012). Escanea en JSON las dos plataformas de la imagen de T06
    (D11). Si aparece una crítica o alta, parar y volver al owner con la lista y la alternativa del riesgo "Alpine no
    llega a 0 altas" del plan.
  - **Hecho cuando:** E3 da 0 críticas y 0 altas en `linux/amd64` y en `linux/arm64`, con o sin fix, y el conteo por
    severidad queda anotado en D11 del plan.

## Phase 4 — Reportes de SCA

- [x] **T08 — SARIF solo con hallazgos corregibles**
  - **RF:** RF-18, RF-19, RF-20, RF-21, RF-23
  - **Depende de:** — (en paralelo con T01, T05 y T06)
  - **Alcance:**
    - En `.github/workflows/release.yaml` (`Scan Image`), por plataforma: se mantiene la corrida a
      `grype-<arch>.json` y se agrega otra `--only-fixed -o sarif=grype-<arch>.sarif`.
    - En `.github/workflows/sca.yaml` (`Scan Lockfile`): JSON completo para el gate y SARIF `--only-fixed`.
    - Las categorías (`sca-image-amd64`, `sca-image-arm64`, `sca-lockfile`), `ci/sca_gate.py` y `codeql.yaml` no
      cambian (D6, D7).
  - **Hecho cuando:**
    - `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest` no reporta errores en los dos workflows;
    - `git diff` no toca `ci/sca_gate.py` ni `codeql.yaml`;
    - `uv run pytest tests/ci_sca_gate_test.py` pasa.

## Phase 5 — Documentación, verificación y release

- [ ] **T09 — README y SECURITY**
  - **RF:** RF-10, RF-18, RF-19, RF-20
  - **Depende de:** T06, T08
  - **Alcance:**
    - `README.md`:
      - CI/CD: qué hallazgos llegan a code scanning;
      - Dependabot: la base es `python:3.14-alpine`;
      - Hallazgos: los que no tienen fix solo aparecen en el resumen del job.
    - `SECURITY.md`, "Análisis de seguridad en CI": imagen Alpine sin gestor de paquetes; Security muestra todo
      CodeQL y solo los hallazgos de Grype con fix.
  - **Hecho cuando:**
    - `grep -n "alpine" README.md SECURITY.md` encuentra la base en las dos;
    - `grep -n "slim" README.md SECURITY.md Dockerfile` no devuelve nada;
    - las dos describen el filtro `--only-fixed` del SARIF.

- [ ] **T10 — Puerta de calidad**
  - **RF:** —
  - **Depende de:** T02, T03, T04, T05, T07, T08, T09
  - **Alcance:** los comandos de `AGENTS.md` sobre la rama completa.
  - **Hecho cuando:**
    - `uv run pytest` pasa completo;
    - `uv run pyright` da 0 errores;
    - `uv run alembic check` no detecta operaciones nuevas;
    - `git status` está limpio salvo los cambios de la spec.

- [ ] **T11 — Versión 3.10.8**
  - **RF:** —
  - **Depende de:** T10
  - **Alcance:** subir `version` en `pyproject.toml` al patch siguiente de la versión de `main` (3.10.8 si no cambió) y
    correr `uv sync`, en un solo commit con `uv.lock`.
  - **Hecho cuando:** el último commit de la rama es `chore(release): Bump version to v3.10.8` y solo cambia
    `pyproject.toml` y `uv.lock`.

- [ ] **T12 — PR y verificación en CI**
  - **RF:** RF-01 a RF-09, RF-18 a RF-21, RF-23
  - **Depende de:** T11
  - **Alcance:** abrir el PR desde `fix/code-scanning-findings` con `/create-pr` y revisar los checks del ruleset de
    `main`.
  - **Hecho cuando:** se cumple E4:
    - pasan `Type Check & Tests`, `Lockfile Scan`, `Analyze (actions)`, `Analyze (python)` y la regla de code
      scanning;
    - CodeQL marca #2, #3 y #4 como corregidas en el PR, o, si no reconoce los sanitizadores, quedan descartadas como
      *false positive* con referencia a D8/D10;
    - el SARIF de `sca-lockfile` solo trae hallazgos con fix, mientras el resumen del job los lista todos.

- [ ] **T13 — Merge, release y comprobación en producción**
  - **RF:** RF-11, RF-12, RF-17, RF-22, RF-23, RF-06
  - **Depende de:** T12
  - **Alcance:** merge con merge commit `chore(release): Version 3.10.8`. Seguir `release.yaml` hasta el deploy, hacer
    la prueba manual en Google Chat y revisar Security y el Dependency graph. Luego `/tag-release`.
  - **Hecho cuando:**
    - se cumple E5: el escaneo del release da 0 críticas/altas en las dos plataformas, el SBOM queda como artefacto,
      la imagen se publica y `rollout status` termina bien;
    - Security queda sin alertas de CodeQL abiertas y sin alertas de Grype abiertas sin fix;
    - se cumple E6: un mensaje con adjunto en Google Chat recibe la respuesta diferida con el archivo leído;
    - se cumple E7: el Dependency graph lista `python:3.14-alpine` en el ecosistema `docker` sin errores;
    - el release `3.10.8` está publicado.

## RF → tasks

| RF | Tasks |
|---|---|
| RF-01, RF-02 | T01, T12 |
| RF-03 | T02, T12 |
| RF-04 | T03, T12 |
| RF-05 | T04, T12 |
| RF-06 | T01, T12, T13 |
| RF-07 | T01, T02, T12 |
| RF-08, RF-09 | T05, T12 |
| RF-10 | T06, T07, T09 |
| RF-11 | T07, T13 |
| RF-12 | T13 |
| RF-13, RF-14, RF-15, RF-16 | T06 |
| RF-17 | T06, T13 |
| RF-18, RF-19, RF-20 | T08, T09, T12 |
| RF-21 | T08, T12 |
| RF-22 | T13 |
| RF-23 | T08, T12, T13 |
