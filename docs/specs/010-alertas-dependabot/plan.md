# Plan 010 — Resolver las alertas de seguridad de Dependabot

- **Spec:** [spec.md](spec.md) (approved 2026-10-22)
- **Status:** Approved
- **Version:** 3.8.1 (fix → patch, desde 3.8.0)

El plan respeta la constitución:
- §1: no agrega ni quita dependencias directas y no toca `pyproject.toml` salvo la versión. Solo cambian en `uv.lock`
  las versiones resueltas de dos transitivas.
- §4: el test nuevo corre en memoria, sin BD ni LLM, y no importa Pillow directamente.
- §5: no hay cambios en `src/models/` ni migraciones.
- §9: el bump a 3.8.1 va al final, en un solo commit con `uv.lock`.
- §10: la imagen se sigue construyendo con `uv sync --locked --no-dev`, sin cambios en el `Dockerfile`.

No requiere aprobaciones nuevas: el archivo nuevo en `.github/` y el cierre del PR #21 ya están aprobados en la spec.

## Modules

### New: `.github/dependabot.yml`
| File | Responsibility | RF |
|---|---|---|
| `.github/dependabot.yml` | `version: 2` con una sola entrada `package-ecosystem: "uv"`, `directory: "/"`, `schedule.interval: "weekly"` y `open-pull-requests-limit: 0` (D3). Sin `commit-message` propio: el prefijo por defecto `build(deps)` ya sigue Conventional Commits, como el PR #21 (D4). | RF-07 a RF-09 |

### Changed
| File | Change | RF |
|---|---|---|
| `uv.lock` | Se regenera con `uv lock --upgrade-package pillow --upgrade-package certifi` (D1): `pillow` 12.3.0 → 13.0.0 y `certifi` 2026.7.22 → 2026.10.19. El diff solo debe tocar los bloques `[[package]]` de esos dos paquetes. | RF-01 a RF-04 |
| `requirements.txt` | Se elimina con `git rm` (D2). | RF-06 |
| `tests/attachments_test.py` | Test nuevo U-01: un `.pptx` con una imagen y un cuadro de texto. `python-pptx` usa Pillow al insertar la imagen, así que el test pasa por Pillow 13. La imagen es un PNG de 1×1 escrito como `bytes` en el test (D5). | RF-05 |
| `README.md` | Sección **CI/CD**: una línea que diga que Dependabot abre PRs de seguridad sobre `uv.lock` (`.github/dependabot.yml`) y que `uv.lock` es la única fuente de dependencias, sin `requirements.txt`. | RF-06, RF-07 |

### Infrastructure
| File | Change |
|---|---|
| `pyproject.toml` / `uv.lock` | Versión `3.8.1` y `uv sync`, en el commit `chore(release): Bump version to v3.8.1` (D7). |
| `Dockerfile`, `.github/workflows/*` | Sin cambios: ya instalan desde `uv.lock` con `--locked` (D8). |

## Decisions
| # | Decision | Discarded alternative | Why |
|---|---|---|---|
| D1 | `uv lock --upgrade-package pillow --upgrade-package certifi`. | `uv lock --upgrade` completo, o fijar mínimos con `constraint-dependencies` en `[tool.uv]`. | RF-03 exige que no cambie nada más, y `--dry-run` ya confirmó que el upgrade selectivo solo toca esos dos paquetes. Las restricciones en `[tool.uv]` agregarían configuración que mantener para algo que `uv.lock` + `--locked` ya fijan. |
| D2 | Eliminar `requirements.txt`. | Regenerarlo con `uv export`. | Decisión de la spec (RF-06). Ni el `Dockerfile` ni el CI lo leen, y duplica cada alerta. |
| D3 | `open-pull-requests-limit: 0` con `schedule` semanal. | Omitir el bloque `schedule`. | La config v2 exige `schedule` en cada entrada. Con límite 0 se desactivan los PRs de versiones y los de seguridad siguen funcionando (RF-08, RF-09). |
| D4 | Usar el `commit-message` por defecto de Dependabot. | Configurar un prefijo propio. | El prefijo por defecto (`build(deps): Bump …`) ya sigue Conventional Commits, como en el PR #21. Una opción menos que mantener. |
| D5 | El test crea la imagen del `.pptx` con un PNG mínimo escrito como `bytes`. | Generar la imagen con `PIL.Image` en el test. | Pillow es transitiva y no está declarada en `pyproject.toml` (§1): el test no debe importarla. Además, el test actual de `.pptx` solo tiene texto y nunca pasa por Pillow, así que no prueba RF-05 con la versión 13. |
| D6 | Cerrar el PR #21 sin fusionar, con un comentario que apunte al PR de esta spec. | Fusionarlo o reutilizar su rama. | Solo cambia `requirements.txt`, que esta spec elimina, y no toca `uv.lock` (RF-04). |
| D7 | Versión patch 3.8.1. | Minor 3.9.0. | No cambia el comportamiento de la API: es un arreglo de seguridad en dependencias (§9, convención fix → patch). |
| D8 | No cambiar el `Dockerfile` ni los workflows. | Agregar un paso de auditoría (`pip-audit`) al CI. | Ya instalan desde `uv.lock` con `--locked`, así que RF-04 se cumple solo. La auditoría agregaría una dependencia y queda fuera de la spec. |
| D9 | Construir la imagen Docker localmente antes del merge. | Confiar en `release.yaml`. | `release.yaml` construye la imagen solo al hacer push a `main` y despliega enseguida. Construirla antes detecta a tiempo un fallo de instalación de Pillow 13 en `python:3.14-slim` (RF-04). |

## RF coverage
| RF | Where | Tests |
|---|---|---|
| RF-01, RF-02 | `uv.lock` | V1 |
| RF-03 | `uv.lock` (D1) | V2 |
| RF-04 | `Dockerfile` sin cambios (D8, D9) | V3 |
| RF-05 | `src/services/attachments.py` sin cambios (`extract_pptx`) | U-01 y el test existente `test_lee_word_excel_powerpoint_y_texto_en_memoria` |
| RF-06 | `requirements.txt` eliminado, `README.md` | V4 |
| RF-07, RF-09 | `.github/dependabot.yml` | V5 |
| RF-08 | `.github/dependabot.yml` | V6 (después del merge) |

## Test strategy

**Unit (`tests/attachments_test.py`)**
- **U-01** `test_u_01_pptx_con_imagen_extrae_solo_el_texto`: una diapositiva con una imagen PNG de 1×1 (`add_picture`) y
  un cuadro de texto. `read_attachments` devuelve `"[Archivo adjunto: con-imagen.pptx]\nDiapositiva 1\n<texto>"`: el
  texto sale y la imagen se ignora, como hoy. El `.pptx` se arma igual que en `pptx_bytes()`.

**Verificaciones (manuales o con comandos, sin test automático)**
- **V1** `uv tree --invert --package pillow` y `--package certifi` muestran 13.0.0 y 2026.10.19.
- **V2** `git diff main -- uv.lock` solo cambia los bloques de `pillow` y `certifi`.
- **V3** `docker build .` termina sin errores, y en la imagen `python -c "import PIL, certifi; print(PIL.__version__, certifi.__version__)"` muestra las versiones parcheadas.
- **V4** `git ls-files requirements.txt` no devuelve nada, y `grep -rn requirements.txt` no encuentra referencias fuera de `docs/specs/`.
- **V5** `.github/dependabot.yml` pasa la validación de GitHub: después del push, la pestaña *Insights → Dependency graph → Dependabot* lista el ecosistema `uv` sin errores.
- **V6** Después del merge, `gh api repos/{owner}/{repo}/dependabot/alerts -q '[.[]|select(.state=="open")]|length'` devuelve 0. RF-08 no se puede probar hasta la próxima alerta real; ver **Risks**.

**Limitaciones:** pytest no puede comprobar el comportamiento de Dependabot ni de la imagen. Eso se cubre con V3, V5 y V6.

## Implementation order
1. Actualizar `uv.lock` (D1) y comprobar V1 y V2.
2. Agregar U-01 y correr `uv run pytest` y `uv run pyright`.
3. Eliminar `requirements.txt` (D2), crear `.github/dependabot.yml` (D3, D4) y actualizar el README.
4. Correr V3 (build de la imagen) y V4. `uv run alembic check` sin diferencias.
5. Al cerrar la spec: bump a 3.8.1 + `uv sync` en el commit de release (D7).

## Release
1. Abrir el PR de `fix/dependabot-alerts`; `test.yaml` debe pasar.
2. Cerrar el PR #21 con un comentario que apunte al PR nuevo (D6).
3. Merge a `main`: `release.yaml` construye la imagen 3.8.1 y la despliega. Sin migraciones.
4. Comprobar V5 y V6, y que `/health` responda en el cluster.
5. `/tag-release` para el tag `3.8.1`.

## Risks
- **Pillow 13 es major:** python-pptx 1.0.2 podría usar una API eliminada. Impacto: fallaría la lectura de `.pptx`
  con imágenes. Mitigación: U-01 lo prueba y V3 lo confirma en la imagen. El usuario descartó posponer pillow:
  si algo falla, se corrige dentro de esta spec.
- **Seguridad de Dependabot con `uv`:** aunque se declare el ecosistema, podría volver a abrir PRs sobre otro
  manifiesto o ninguno. Impacto: las alertas futuras quedarían sin PR automático. Mitigación: V5 confirma que GitHub
  reconoce la config; si el próximo PR no toca `uv.lock`, se resuelve a mano como en esta spec.
- **Alertas que no se cierran solas:** las de `requirements.txt` se cierran cuando GitHub reescanea `main` sin el
  archivo. Mitigación: V6; si quedan abiertas, se cierran con `gh api` como `fixed`/`not used`.
