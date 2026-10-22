# Spec 010 — Resolver las alertas de seguridad de Dependabot

- **Status:** Approved
- **Date:** 2026-10-22
- **Scope:** virtual-assistant — dependencias de Python (`uv.lock`), imagen Docker y configuración de Dependabot en
  GitHub

## Por qué

GitHub reporta 49 alertas abiertas de Dependabot sobre dos dependencias transitivas. La imagen que corre en
producción instala `pillow` 12.3.0, que llega por la lectura de adjuntos PowerPoint y tiene vulnerabilidades
críticas (desbordamientos de memoria al decodificar JPEG y TIFF, inyección de argumentos en Ghostscript con posible
ejecución remota y bombas de descompresión), y `certifi` 2026.7.22, que todavía confía en CAs raíz emSign cuyas claves
intermedias fueron comprometidas y que se usa en las conexiones HTTPS a Google GenAI y LangSmith.

Cada alerta sale dos veces porque el repo versiona un `requirements.txt` exportado de `uv.lock` que ni la imagen ni
el CI usan. Las *security updates* de Dependabot están activas, pero el PR que abrieron (#21) actualiza solo ese
`requirements.txt` y deja `uv.lock`, y con él la imagen, en las versiones vulnerables.

## Decisiones

| Tema | Decisión |
|---|---|
| `requirements.txt` | Se elimina: `uv.lock` queda como única fuente de las versiones instaladas y desaparecen las alertas duplicadas. |
| Alcance de la actualización | Solo `pillow` (→ 13.0.0) y `certifi` (→ 2026.10.19); las demás dependencias quedan en sus versiones actuales. |
| Configuración de Dependabot | Se agrega una configuración que declara el ecosistema `uv` en la raíz, para que los PRs de seguridad actualicen `uv.lock`, sin PRs programados de versiones nuevas. |
| PR #21 de Dependabot | Se cierra sin fusionar, porque esta spec lo reemplaza y el archivo que modifica deja de existir. |
| Criterio de cierre | Cero alertas abiertas de Dependabot después del merge en `main` (decidido por defecto). |
| Versión | Patch `3.8.1`: no cambia el comportamiento de la API (decidido por defecto). |

## Requisitos funcionales

"El sistema" es el repositorio virtual-assistant y la imagen Docker que se construye a partir de él; "versiones
parcheadas" son `pillow` ≥ 13.0.0 y `certifi` ≥ 2026.10.19.

### Dependencias instaladas

- **RF-01** — El sistema debe resolver `pillow` en una versión ≥ 13.0.0.
- **RF-02** — El sistema debe resolver `certifi` en una versión ≥ 2026.10.19.
- **RF-03** — El sistema debe mantener sin cambios las versiones resueltas de las demás dependencias.
- **RF-04** — Cuando se construya la imagen Docker, el sistema debe instalar las versiones parcheadas de `pillow` y
  `certifi`.

### Comportamiento existente

- **RF-05** — Cuando un usuario adjunte un archivo PowerPoint (`.pptx`) en el chat, el sistema debe extraer su texto
  igual que antes de la actualización.

### Fuente única de dependencias

- **RF-06** — El sistema debe declarar las versiones instaladas solo en `uv.lock`, sin un `requirements.txt`
  versionado.

### Dependabot

- **RF-07** — El sistema debe declarar a Dependabot el ecosistema `uv` en la raíz del repositorio.
- **RF-08** — Cuando Dependabot detecte una vulnerabilidad en una dependencia de Python, debe abrir el PR de seguridad
  sobre `uv.lock`.
- **RF-09** — El sistema no debe hacer que Dependabot abra PRs programados de versiones nuevas que no correspondan a
  una alerta de seguridad.

## Fuera de alcance

- Actualizar otras dependencias (por ejemplo, `uv lock --upgrade`): mezcla cambios ajenos a la seguridad y aumenta el
  riesgo de regresiones en LangChain y Google GenAI.
- Updates de versiones de GitHub Actions o de la imagen base de Docker por Dependabot.
- Fusionar el PR #21 de Dependabot.
- Cambios de comportamiento en la API, en el esquema o en la lectura de adjuntos.

## Restricciones

- No se agregan ni se quitan dependencias directas en `pyproject.toml`: solo cambian las versiones resueltas de dos
  transitivas (constitución, principio 1).
- Sin cambios en `src/models/` ni migraciones (principio 5).
- El archivo de configuración de Dependabot es nuevo y va en `.github/`, fuera de `docs/`.

## Criterios de finalización

- [ ] `uv run pytest` en verde, incluidos los tests de lectura de `.pptx` (RF-05).
- [ ] `uv run pyright` sin errores.
- [ ] `uv run alembic check` sin diferencias.
- [ ] `uv tree --invert --package pillow` y `--package certifi` muestran las versiones parcheadas (RF-01, RF-02) y
      el diff de `uv.lock` solo cambia esos dos paquetes (RF-03).
- [ ] La imagen Docker se construye con `uv sync --locked --no-dev` (RF-04).
- [ ] Versión subida a `3.8.1` con `uv sync`, en un solo commit `chore(release): Bump version to v3.8.1`.
- [ ] PR #21 cerrado sin fusionar.
- [ ] Después del merge en `main`, la API de GitHub reporta cero alertas abiertas de Dependabot.
