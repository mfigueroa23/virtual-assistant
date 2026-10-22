# Tareas 010 — Resolver las alertas de seguridad de Dependabot

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Orden:** por dependencia. Una tarea solo empieza cuando las de "Depende de" están marcadas. Las tareas con las
  mismas dependencias pueden ir en paralelo.
- **Ids:** `D#` = decisión del plan, `U-*` / `V#` = tests y verificaciones de la estrategia de tests del plan.
- Cada tarea que cambia comportamiento escribe primero sus tests (constitución 4) y deja `uv run pyright` en 0 errores
  (constitución 3). La versión solo sube en T05.

## Grafo

```
T01 ─┬─► T02 ─┬─► T04 ─► T05 ─► T06
     └─► T03 ─┘
          (T02 y T03 pueden ir en paralelo)
```

## Fase 0 — Gate

- [x] **T01 — Cerrar la aprobación de la spec y del plan**
  - **RF:** —
  - **Depende de:** —
  - **Alcance:** el usuario aprobó la spec y el plan el 2026-10-22, y descartó que Pillow 13 rompa python-pptx.
    - Pasar el `Status` de `spec.md` y `plan.md` a `Approved`.
    - En **Risks** del plan, quitar la salida de posponer pillow y publicar solo certifi.

    Hacer commit de la carpeta de la spec en `fix/dependabot-alerts`.
  - **Hecho cuando:** `spec.md` y `plan.md` muestran `Status: Approved` y `git log` muestra el commit con `spec.md`,
    `plan.md` y `tasks.md`.

## Fase 1 — Dependencias

- [ ] **T02 — Actualizar pillow y certifi en `uv.lock`**
  - **RF:** RF-01, RF-02, RF-03, RF-04, RF-05
  - **Depende de:** T01 (puede ir en paralelo con T03)
  - **Alcance:**
    - Escribir U-01 en `tests/attachments_test.py`: un `.pptx` con una imagen PNG de 1×1 escrita como `bytes` y un
      cuadro de texto, armado igual que `pptx_bytes()`, sin importar `PIL` (D5).
    - Ejecutar `uv lock --upgrade-package pillow --upgrade-package certifi` (D1) y `uv sync`.

    No subir la versión.
  - **Hecho cuando:**
    - **V1:** `uv tree --invert --package pillow` muestra 13.0.0 y `--package certifi` muestra 2026.10.19.
    - **V2:** `git diff main -- uv.lock` solo cambia los bloques de `pillow` y `certifi`.
    - `uv run pytest` pasa con U-01 y `test_lee_word_excel_powerpoint_y_texto_en_memoria`.
    - `uv run pyright` termina con 0 errores.

## Fase 2 — Fuente única y Dependabot

- [ ] **T03 — Eliminar `requirements.txt`, configurar Dependabot y actualizar el README**
  - **RF:** RF-06, RF-07, RF-08, RF-09
  - **Depende de:** T01 (puede ir en paralelo con T02)
  - **Alcance:**
    - `git rm requirements.txt` (D2).
    - Crear `.github/dependabot.yml` con `version: 2` y una entrada `package-ecosystem: "uv"`, `directory: "/"`,
      `schedule.interval: "weekly"` y `open-pull-requests-limit: 0`, sin `commit-message` propio (D3, D4).
    - En `README.md`, sección **CI/CD**: Dependabot abre PRs de seguridad sobre `uv.lock`, y `uv.lock` es la única
      fuente de dependencias.
  - **Hecho cuando:**
    - **V4:** `git ls-files requirements.txt` no devuelve nada y `grep -rn requirements.txt --exclude-dir=.venv --exclude-dir=docs .` no encuentra referencias.
    - `.github/dependabot.yml` es YAML válido: `uv run python -c "import yaml; yaml.safe_load(open('.github/dependabot.yml'))"` no falla.
    - El README menciona `.github/dependabot.yml`.

## Fase 3 — Verificación y release

- [ ] **T04 — Puerta de calidad y build de la imagen**
  - **RF:** RF-04
  - **Depende de:** T02, T03
  - **Alcance:** checklist de `AGENTS.md` y build local de la imagen (D8, D9).
  - **Hecho cuando:**
    - `uv run pytest` y `uv run pyright` en verde.
    - `uv run alembic check` sin diferencias.
    - **V3:** `docker build -t virtual-assistant:010 .` termina sin errores, y
      `docker run --rm --entrypoint python virtual-assistant:010 -c "import PIL, certifi; print(PIL.__version__, certifi.__version__)"`
      muestra `13.0.0 2026.10.19`.

- [ ] **T05 — Subir la versión a 3.8.1**
  - **RF:** —
  - **Depende de:** T04
  - **Alcance:** `version = "3.8.1"` en `pyproject.toml` y `uv sync` (D7, constitución 9).
  - **Hecho cuando:** `git log -1` muestra `chore(release): Bump version to v3.8.1` con `pyproject.toml` y `uv.lock`,
    y `uv run pytest` sigue en verde.

- [ ] **T06 — PR, cierre del #21 y comprobación después del merge**
  - **RF:** RF-07, RF-08, RF-09
  - **Depende de:** T05
  - **Alcance:**
    1. `/create-pr` desde `fix/dependabot-alerts`.
    2. Cerrar el PR #21 sin fusionar, con un comentario que apunte al PR nuevo (D6).
    3. Después del merge: comprobar el despliegue de `release.yaml` y ejecutar `/tag-release` para el tag `3.8.1`.
  - **Hecho cuando:**
    - `test.yaml` pasa en el PR y `gh pr view 21 --json state` muestra `CLOSED`.
    - **V5:** *Insights → Dependency graph → Dependabot* lista el ecosistema `uv` sin errores.
    - **V6:** `gh api repos/{owner}/{repo}/dependabot/alerts -q '[.[]|select(.state=="open")]|length'` devuelve 0.
    - `/health` responde en el cluster y el release `3.8.1` existe en GitHub.

## RF → tareas

| RF | Tareas |
|---|---|
| RF-01 | T02 |
| RF-02 | T02 |
| RF-03 | T02 |
| RF-04 | T02, T04 |
| RF-05 | T02 |
| RF-06 | T03 |
| RF-07 | T03, T06 |
| RF-08 | T03, T06 |
| RF-09 | T03, T06 |
