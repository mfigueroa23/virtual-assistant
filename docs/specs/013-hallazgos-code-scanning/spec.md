# Spec 013 — Remediación de los hallazgos de code scanning

- **Status:** Approved
- **Date:** 2026-10-28
- **Scope:** virtual-assistant — llamadas a Google Chat (`services`), logs de las tarjetas de conocimiento, imagen
  Docker y reportes de SCA (`release.yaml`, `sca.yaml`); sin cambios en endpoints, esquema ni properties

## Por qué

Tras activar CodeQL y el SCA (spec 012), la pestaña Security de `main` tiene 325 alertas abiertas. Una sola es un
problema del código y es crítica: CodeQL `py/partial-ssrf` (#4), porque el nombre del space que llega en el evento de
Google Chat se usa tal cual en la ruta de la URL a la que se envía el token de la cuenta de servicio. La descarga de
adjuntos hace lo mismo con el nombre del recurso. A eso se suman dos alertas medias `py/log-injection`: el id de
propuesta del botón de una tarjeta se escribe en el log sin limpiar.

Las otras 322 son de Grype sobre la imagen publicada: 161 CVEs por plataforma, de ellas 6 críticas y 126 altas en
total, todas en paquetes de Debian 13 que trae `python:3.14-slim` (libc, util-linux, perl-base, ncurses, tar…) y que
la API no usa. Ninguna tiene versión corregida, así que el gate del release no las bloquea, pero tampoco se pueden
resolver actualizando paquetes. Como se suben todas a code scanning, la única alerta accionable quedó enterrada entre
cientos que no lo son, y quien revisa Security no puede distinguir qué requiere acción.

## Decisiones

| Tema | Decisión |
|---|---|
| Estrategia para la imagen | La etapa final usa una imagen base Python 3.14 mínima, sin los paquetes del sistema que la API no usa, en vez de `python:3.14-slim`. Descartadas: recortar `slim` (libc, tar, coreutils y util-linux son esenciales en Debian y se quedan) y solo actualizar el digest. |
| Meta de la imagen | La imagen publicada por esta versión no tiene vulnerabilidades críticas ni altas en ninguna plataforma, con o sin versión corregida. Descartadas: "sin críticas y altas a la baja" y "solo quitar lo no usado". |
| Qué llega a code scanning | Solo los hallazgos de Grype con versión corregida, de la imagen y de `uv.lock`. Los que no tienen corrección quedan en el resumen del job y en el reporte JSON. Descartadas: subir todas las críticas/altas, o subir todo con la severidad de Debian. |
| Identificador inválido de Google Chat | No se hace la llamada a Google con ese valor y el flujo sigue por su camino de fallo actual; el webhook responde como hoy. Descartado: rechazar el evento con 400. |
| Alertas medias de log injection | Entran en esta spec. |
| Gate del SCA | Sin cambios: sigue bloqueando las críticas o altas con versión corregida y sin excepción vigente (spec 012, por defecto). |
| Otros repos | Solo virtual-assistant; `portfolio`, `panel-portfolio` y `api-portfolio` quedan para su propia spec (por defecto). |
| Versión de esta spec | Patch sobre la versión de `main` al cerrar (corrección de seguridad, sin capacidad nueva), según el checklist de cierre (por defecto). |

## Requisitos funcionales

"El sistema" es la API virtual-assistant y su imagen Docker; "el pipeline" son los workflows de GitHub Actions del
repositorio. "Nombre de space válido" es `spaces/<id>`, donde `<id>` tiene solo letras, dígitos, `_` o `-`. "Nombre de
adjunto válido" es un nombre de recurso de Google Chat formado por segmentos separados por `/`, que no empieza con `/`
y no contiene segmentos `.` o `..`, `?`, `#`, `%`, espacios ni caracteres de control. "La imagen" es la imagen
`virtual-assistant` para `linux/amd64` y `linux/arm64`. "Hallazgo con corrección" es una vulnerabilidad de Grype que
informa al menos una versión corregida.

### Llamadas a Google Chat

- **RF-01** — El sistema debe enviar el token de la cuenta de servicio de Google Chat solo a URLs con host `chat.googleapis.com`.
- **RF-02** — Cuando el sistema publique un mensaje en Google Chat con un nombre de space válido, debe publicarlo como hoy, incluido el hilo si corresponde.
- **RF-03** — Si el evento de una respuesta diferida trae un nombre de space que no es válido, entonces el sistema no debe llamar a Google Chat y debe registrar un error en el log sin el valor recibido, como cuando el evento no trae el space.
- **RF-04** — Si la property `whatsapp_notify_space` no es un nombre de space válido, entonces el sistema no debe publicar el aviso de WhatsApp, no debe reintentarlo y debe registrar un error en el log sin el valor de la property.
- **RF-05** — Si el hilo en el que se publicaría una propuesta de conocimiento no pertenece a un nombre de space válido, entonces el sistema no debe publicar la propuesta y debe registrar un error en el log sin el valor del hilo.
- **RF-06** — Cuando un mensaje traiga un adjunto con nombre de adjunto válido, el sistema debe descargarlo y leerlo como hoy.
- **RF-07** — Si un adjunto trae un nombre de adjunto que no es válido, entonces el sistema no debe llamar a Google Chat para descargarlo y debe tratarlo como un adjunto que no se pudo leer, sin cambiar el resto de la respuesta.

### Logs de las tarjetas de conocimiento

- **RF-08** — Cuando el sistema registre en el log un error al resolver un botón de una tarjeta de conocimiento, debe escribir el id de propuesta sin saltos de línea ni caracteres de control, de modo que cada error ocupe una sola línea.
- **RF-09** — Cuando se presione un botón de una tarjeta de conocimiento con un id de propuesta existente, el sistema debe aplicar o descartar la propuesta y devolver la tarjeta como hoy.

### Imagen

- **RF-10** — La imagen debe construirse sobre una imagen base Python 3.14 mínima, fijada por digest, que no incluya perl, util-linux, login, ncurses ni un gestor de paquetes del sistema.
- **RF-11** — El escaneo del release de la imagen publicada por la versión de esta spec no debe reportar vulnerabilidades críticas ni altas en `linux/amd64` ni en `linux/arm64`.
- **RF-12** — El pipeline debe seguir construyendo, escaneando y publicando la imagen para `linux/amd64` y `linux/arm64`, con un SBOM por plataforma como artefacto del release.
- **RF-13** — Cuando arranque el contenedor, el sistema debe aplicar las migraciones pendientes de Alembic antes de levantar la API, sin pasos manuales.
- **RF-14** — Si las migraciones fallan al arrancar, entonces el contenedor debe terminar con un código de salida distinto de cero sin levantar la API.
- **RF-15** — El contenedor debe ejecutar la API con un usuario sin privilegios.
- **RF-16** — Mientras el contenedor esté en marcha, `/health` debe responder `200` y el healthcheck de la imagen debe reportarlo sano.
- **RF-17** — Dependabot debe seguir detectando la imagen base del `Dockerfile` y proponiendo un PR cuando cambie su digest.

### Reportes de SCA

- **RF-18** — Cuando el release suba el escaneo de la imagen a code scanning, debe incluir solo hallazgos con corrección.
- **RF-19** — Cuando el escaneo de `uv.lock` suba sus resultados a code scanning, debe incluir solo hallazgos con corrección.
- **RF-20** — El resumen del job de cada escaneo debe seguir listando todos los hallazgos, con y sin corrección, con su estado de bloqueo.
- **RF-21** — El gate del SCA debe seguir bloqueando los hallazgos críticos o altos con corrección que no tengan una excepción vigente, igual que hoy.
- **RF-22** — Cuando termine en `main` el primer escaneo de la imagen y de `uv.lock` posterior a esta spec, code scanning no debe tener alertas de Grype abiertas sin corrección.
- **RF-23** — El pipeline debe seguir publicando en code scanning todas las alertas de CodeQL, sin filtrarlas.

## Fuera de alcance

- Rechazar con `400` los eventos con un space o adjunto inválido (descartado: un adjunto raro dejaría al usuario sin
  respuesta).
- Recortar `python:3.14-slim` o solo actualizar su digest (descartados a favor de una base mínima).
- Subir a code scanning los hallazgos sin corrección, o reescribir su severidad (descartado: solo se publica lo
  accionable).
- Que el gate bloquee hallazgos sin corrección: la meta de 0 críticas/altas (RF-11) se verifica en esta versión, no se
  convierte en una regla permanente del pipeline.
- Los mismos cambios en `portfolio`, `panel-portfolio` y `api-portfolio`.
- Cambios en los endpoints, sus respuestas, el esquema, las properties o la verificación del ID token de Google.
- Cambios en los rulesets de `main`, que ya exigen CodeQL, SCA y tests.

## Restricciones

- **Aprobación de infra:** la imagen base nueva (proveedor, licencia, disponibilidad para `amd64` y `arm64` y que
  Dependabot la soporte) y los cambios en `Dockerfile`, `entrypoint.sh`, `release.yaml` y `sca.yaml` necesitan la
  aprobación del owner antes de planificar.
- **Riesgo de la base mínima:** `entrypoint.sh` usa `/bin/sh` y el healthcheck usa `python`; el plan debe verificar
  que la base elegida conserva lo necesario para RF-13 a RF-16, o justificar el reemplazo, y que `asyncpg` y
  `greenlet` funcionan con su libc.
- **Dependencias:** no se agregan dependencias de Python (constitución §1); si el plan necesita una, es una
  aprobación aparte.
- Constitución §7: los errores nuevos solo van al log, nunca al cliente; §10: la imagen arranca sin pasos manuales y
  expone `/health`.
- Spec 012: el gate, las excepciones de `.github/sca-exceptions.json`, el SBOM y el despliegue por digest no cambian.
- Sin cambios en `src/models/` ni migraciones (§5).

## Criterios de finalización

- [ ] Tests de RF-01 a RF-09: space válido e inválido (evento, property y hilo), adjunto válido e inválido
      (`..`, `?`, `%`, `/` inicial) y un id de propuesta con saltos de línea.
- [ ] `uv run pytest` y `uv run pyright` en verde; `uv run alembic check` sin diferencias.
- [ ] La imagen construida en local para las dos plataformas aplica las migraciones al arrancar, corre sin
      privilegios, responde `200` en `/health` y termina con error si la base de datos no está disponible.
- [ ] El release de la versión nueva escanea la imagen sin críticas ni altas en ambas plataformas, sube el SBOM,
      publica, despliega por digest y pasa `rollout status`.
- [ ] Tras ese release, Security no tiene alertas de CodeQL abiertas ni alertas de Grype abiertas sin corrección.
- [ ] Un mensaje de prueba con un adjunto en Google Chat recibe la respuesta diferida con el archivo leído.
- [ ] README y `SECURITY.md` describen la base nueva y qué hallazgos llegan a code scanning.
- [ ] Versión subida con un patch y `uv sync`, en un solo commit `chore(release): Bump version to vX.Y.Z`.
