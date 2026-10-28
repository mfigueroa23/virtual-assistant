# Spec 014 — Limpieza de las alertas de code scanning

- **Status:** Approved
- **Date:** 2026-10-28
- **Scope:** virtual-assistant: llamadas a Google Chat (`services`), versión de Python del proyecto (desarrollo, CI e
  imagen Docker), `uv.lock` y documentación del stack. Sin cambios en endpoints, esquema ni properties.

## Por qué

Después de la spec 013, la pestaña Security de `main` (v3.10.8) sigue con 5 alertas abiertas. La crítica es la de
CodeQL `py/partial-ssrf` (#4) en la publicación de mensajes en Google Chat. La spec 013 ya valida el nombre del space
antes de armar la URL, pero CodeQL no reconoce esa validación, así que la alerta sigue abierta aunque el riesgo esté
mitigado. Mientras siga ahí, Security muestra una crítica que no se puede distinguir de una real.

Las otras 4 son de Grype sobre la imagen publicada: dos CVEs medias del intérprete `python 3.14.10`, repetidas en
`linux/amd64` y `linux/arm64`. Grype las informa con corrección, así que pasan el filtro de la spec 013. Esa corrección
solo existe en Python 3.15, y el proyecto está fijado en 3.14, así que con la versión actual no se pueden cerrar. Quien
revisa Security ve alertas accionables que, en la práctica, no tienen arreglo dentro del stack actual.

## Decisiones

| Tema | Decisión |
|---|---|
| Alerta CodeQL #4 | Se cambia el código para que CodeQL reconozca la validación y cierre la alerta como corregida. Descartado: descartarla como falso positivo, o intentar el código y descartarla si no basta. |
| Alertas de Grype sobre Python | El proyecto migra a Python 3.15 en esta spec. Descartados: no subir a code scanning los hallazgos cuya única corrección es de otra minor, descartarlas a mano, o esperar un patch de 3.14. |
| Dependencias sin ruedas 3.15 para musl (`pyyaml`, `zstandard`) | Se compilan en la etapa de build de la imagen. La imagen final no lleva compilador. Las que tienen ruedas 3.15 en una versión más nueva se actualizan a esa versión. |
| Si la migración resulta inviable | Si una dependencia no funciona en 3.15 o exige cambios grandes (reemplazar librerías, cambiar código de dominio), se detiene la spec y se revisa con el owner. No se vuelve a 3.14 automáticamente. |
| Meta de Security | Al cerrar la spec, code scanning de `main` no tiene alertas abiertas, ni de CodeQL ni de Grype. Si la imagen 3.15 trae hallazgos nuevos con corrección, se corrigen en esta spec. Descartado: cerrar solo las 5 actuales. |
| Filtro y gate del SCA | Sin cambios: se suben solo los hallazgos con corrección y el gate bloquea las críticas y altas con corrección (specs 012 y 013, por defecto). |
| Otros repos | Solo virtual-assistant (por defecto). |
| Versión de esta spec | Minor sobre la versión de `main` al cerrar, porque cambia el runtime de Python (por defecto). |

## Requisitos funcionales

"El sistema" es la API virtual-assistant y su imagen Docker, y "el pipeline" son los workflows de GitHub Actions del
repositorio. "Nombre de space válido" tiene la definición de la spec 013 (`spaces/<id>`, donde `<id>` solo tiene
letras, dígitos, `_` o `-`). "La imagen" es la imagen `virtual-assistant` para `linux/amd64` y `linux/arm64`.
"Hallazgo con corrección" es un hallazgo de Grype que informa al menos una versión corregida.

### Publicación en Google Chat

- **RF-01** — Cuando termine en `main` el primer análisis de CodeQL posterior a esta spec, la alerta `py/partial-ssrf` #4 debe quedar cerrada como corregida, sin descartarla a mano.
- **RF-02** — El sistema debe enviar el token de la cuenta de servicio de Google Chat solo a URLs con host `chat.googleapis.com`.
- **RF-03** — Cuando el sistema publique un mensaje en Google Chat con un nombre de space válido, debe publicarlo como hoy, incluido el hilo si corresponde.
- **RF-04** — Si el sistema va a publicar un mensaje con un nombre de space que no es válido, entonces no debe llamar a Google Chat y cada flujo debe seguir su camino de fallo actual (RF-03 a RF-05 de la spec 013).

### Python 3.15

- **RF-05** — El sistema debe ejecutarse con Python 3.15 en desarrollo local, en los tests del CI y en la imagen.
- **RF-06** — La imagen debe construirse sobre una imagen base Python 3.15 Alpine fijada como `tag@digest`.
- **RF-07** — La imagen debe instalar todas las dependencias de producción de `uv.lock` en `linux/amd64` y `linux/arm64`, también las que no publican ruedas 3.15 para musl.
- **RF-08** — La etapa final de la imagen no debe incluir compilador, cabeceras de desarrollo, ncurses, readline ni gestor de paquetes del sistema.
- **RF-09** — El sistema debe mantener las rutas, los status y las respuestas actuales de sus endpoints.
- **RF-10** — Cuando arranque el contenedor, el sistema debe aplicar las migraciones pendientes de Alembic antes de levantar la API, sin pasos manuales.
- **RF-11** — Si las migraciones fallan al arrancar, entonces el contenedor debe terminar con un código de salida distinto de cero sin levantar la API.
- **RF-12** — El contenedor debe ejecutar la API con un usuario sin privilegios.
- **RF-13** — Mientras el contenedor esté en marcha, `/health` debe responder `200` y el healthcheck de la imagen debe reportarlo sano.
- **RF-14** — Dependabot debe seguir detectando la imagen base del `Dockerfile` y proponiendo un PR cuando cambie su digest.

### Code scanning

- **RF-15** — Cuando termine en `main` el primer escaneo de la imagen posterior a esta spec, las alertas de Grype #34, #44, #196 y #206 deben quedar cerradas como corregidas.
- **RF-16** — Cuando terminen en `main` el release de esta spec y sus análisis de CodeQL, de `uv.lock` y de la imagen, code scanning no debe tener alertas abiertas.
- **RF-17** — El pipeline debe seguir subiendo a code scanning solo los hallazgos de Grype con corrección, de la imagen y de `uv.lock`.
- **RF-18** — El pipeline debe seguir subiendo a code scanning todas las alertas de CodeQL, sin filtrarlas.
- **RF-19** — El gate del SCA debe seguir bloqueando los hallazgos críticos o altos con corrección que no tengan una excepción vigente, igual que hoy.

## Fuera de alcance

- Descartar a mano cualquier alerta (descartado: las 5 deben cerrarse como corregidas).
- No subir a code scanning los hallazgos cuya corrección es de otra minor de Python (descartado a favor de migrar).
- Volver a una base Debian o cambiar de distribución de la imagen.
- Actualizar dependencias más allá de lo que exige Python 3.15 o la meta de RF-16.
- Los mismos cambios en `portfolio`, `panel-portfolio` y `api-portfolio`.
- Cambios en los endpoints, sus respuestas, el esquema, las properties o la verificación del ID token de Google.

## Restricciones

- **Aprobación de docs:** la constitución (§1, "Python 3.14") y `AGENTS.md` ("Python 3.14") pasan a Python 3.15.
  Requiere la aprobación del owner, porque `AGENTS.md` protege `docs/`.
- **Aprobación de `uv.lock`:** la migración actualiza `requires-python` y las versiones de `ormsgpack`, `uuid-utils` y
  `websockets` dentro de una tarea intermedia. Es una excepción a la regla de `AGENTS.md` de no regenerar `uv.lock` en
  tareas intermedias. El bump de versión sigue yendo solo en el commit final.
- **Aprobación de infra:** la nueva imagen base y la etapa de build con herramientas de compilación necesitan la
  aprobación del owner antes de planificar.
- **Riesgo:** el plan debe verificar que `pyyaml` (con o sin su extensión C) y `zstandard` compilan en Alpine para
  las dos plataformas, y que LangChain, LangGraph, asyncpg y greenlet funcionan en 3.15. Si no, se aplica la decisión
  "Si la migración resulta inviable".
- **Dependencias:** no se agregan dependencias de Python (constitución §1).
- Constitución §3: pyright sin errores con 3.15. §4: tests sin BD real ni LLM. §10: la imagen arranca sin pasos
  manuales y expone `/health`.
- Se mantienen RF-01 a RF-09 y RF-12 a RF-23 de la spec 013, además del gate, las excepciones, el SBOM y el despliegue
  por digest de la spec 012.
- Sin cambios en `src/models/` ni migraciones (§5).

## Criterios de finalización

- [ ] Tests de RF-02 a RF-04: space válido con y sin hilo, space inválido sin request a Google Chat.
- [ ] `uv run pytest` y `uv run pyright` en verde con Python 3.15. `uv run alembic check` sin diferencias.
- [ ] CodeQL sobre la rama no reporta `py/partial-ssrf` en `src/services/google_chat.py`.
- [ ] La imagen construida en local para las dos plataformas aplica las migraciones al arrancar, corre sin
      privilegios, responde `200` en `/health`, no tiene compilador en la etapa final y termina con error si la base
      de datos no está disponible.
- [ ] El release de la versión nueva escanea la imagen, sube el SBOM, publica, despliega por digest y pasa
      `rollout status`.
- [ ] Tras ese release, Security de `main` tiene 0 alertas abiertas (RF-15, RF-16).
- [ ] Un mensaje de prueba en Google Chat recibe la respuesta diferida en su hilo.
- [ ] README, `SECURITY.md`, `AGENTS.md` y la constitución dicen Python 3.15 (README:284 ya no dice que se cierran
      los PRs de 3.15).
- [ ] Versión subida con un minor y `uv sync`, en un solo commit `chore(release): Bump version to vX.Y.Z`.
