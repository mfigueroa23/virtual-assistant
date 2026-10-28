# Spec 012 — DevSecOps: CodeQL, Dependabot y SCA

- **Status:** Approved
- **Date:** 2026-10-27
- **Scope:** virtual-assistant — solo CI (GitHub Actions, Dependabot, ajustes de seguridad del repositorio);
  sin cambios en el comportamiento de la API

## Por qué

El análisis estático y las actualizaciones de dependencias dependen hoy de los valores por defecto de GitHub:
CodeQL corre en *default setup* una vez por semana con la suite `default` y nunca bloquea un PR, y Dependabot solo
abre PRs de seguridad sobre `uv.lock` (spec 010), sin actualizar las GitHub Actions ni la imagen base
`python:3.14-slim`. El proyecto no controla de forma versionada qué se analiza, con qué frecuencia ni qué bloquea un
merge, y cada merge a `main` publica la imagen y la despliega en el cluster con `release.yaml`, así que una
dependencia o una imagen base vulnerable puede llegar a producción sin que nada la detenga.

Además, los PRs de Dependabot rompen la regla de entrega (constitución §9 y checklist de cierre de `AGENTS.md`):
cada cambio a `main` debe subir la versión en `pyproject.toml` junto con `uv.lock`, y esos PRs nunca lo hacen, por lo
que el owner tiene que agregar el bump a mano. Por último, no hay análisis de composición de software (SCA) de
`uv.lock` ni de la imagen publicada, ni un inventario (SBOM) de lo que contiene cada imagen.

## Decisiones

| Tema | Decisión |
|---|---|
| Setup de CodeQL | Un setup avanzado versionado reemplaza el *default setup*; lenguajes `actions` y `python`. |
| Consultas de CodeQL | Suite `security-extended` (por defecto, igual que el portafolio). |
| Disparadores de CodeQL | Cada PR a `main`, cada push a `main` y un escaneo programado **diario** (por defecto). |
| Bloqueo de CodeQL | Una alerta nueva de severidad de seguridad alta o crítica falla el check y bloquea el merge (por defecto). |
| Ecosistemas de Dependabot | `uv`, GitHub Actions y las imágenes base del `Dockerfile`. |
| Updates de Dependabot | Semanales en los tres ecosistemas; menores y patch agrupados en un PR por ecosistema y cada mayor en su propio PR; los PRs de seguridad siguen abriéndose de inmediato. **Reemplaza** la decisión de la spec 010 (`open-pull-requests-limit: 0`). |
| Seguridad de imágenes Docker | GitHub no publica advisories de imágenes de contenedor; la imagen base se cubre con los updates semanales y el escaneo de la imagen en el release. |
| Bump de versión | Cada PR de Dependabot recibe un commit `chore(release): Bump version to vX.Y.Z` con un bump **patch** sobre la versión de `main`, que cambia `pyproject.toml` y la entrada del proyecto en `uv.lock`, sea cual sea el tipo de update. |
| Bump desactualizado | El bump se recalcula cuando cambia la versión de `main` mientras el PR está abierto (por defecto). |
| Auto-merge | Ninguno: cada PR de Dependabot espera la revisión del owner, porque cada merge despliega (por defecto). |
| Alcance del SCA | Vulnerabilidades de `uv.lock` en cada PR a `main` y a diario; vulnerabilidades de la imagen en el release **antes de publicarla**; un SBOM de cada imagen publicada. |
| Bloqueo del SCA | Bloquean los hallazgos críticos o altos **con versión corregida**; los que no tienen corrección solo se reportan (por defecto). |
| Excepciones | Un archivo versionado de excepciones; cada entrada lleva el id del hallazgo, un motivo y una fecha de vencimiento (por defecto). |
| Reportes | Los hallazgos de CodeQL y SCA aparecen en la pestaña Security (code scanning) y en el resumen del job (por defecto). |
| Versión de esta spec | Minor `3.10.0` (nueva capacidad de CI), según el checklist de cierre (por defecto). |

## Requisitos funcionales

"El pipeline" es el conjunto de workflows de GitHub Actions y la configuración de Dependabot versionados en este
repositorio. "Owner" es el dueño del repositorio. "PR de Dependabot" es un pull request abierto por Dependabot contra
`main`. "Hallazgo bloqueante" es una vulnerabilidad de severidad crítica o alta que tiene versión corregida y no está
cubierta por una excepción vigente. "La imagen" es la imagen `virtual-assistant` que construye el `Dockerfile` para
`linux/amd64` y `linux/arm64`.

### SAST (CodeQL)

- **RF-01** — El pipeline debe analizar el repositorio con CodeQL para los lenguajes `actions` y `python` usando la suite `security-extended`.
- **RF-02** — Cuando se abra o actualice un PR a `main`, el pipeline debe ejecutar el análisis de CodeQL y reportarlo como un check del PR.
- **RF-03** — Cuando se haga push a `main`, el pipeline debe ejecutar el análisis de CodeQL.
- **RF-04** — El pipeline debe ejecutar el análisis de CodeQL sobre `main` una vez al día de forma programada.
- **RF-05** — Cuando termine un análisis de CodeQL, el pipeline debe publicar sus alertas en el code scanning del repositorio (pestaña Security).
- **RF-06** — Si un PR introduce una alerta de CodeQL de severidad de seguridad alta o crítica, entonces el check de CodeQL debe fallar y el PR no debe poder fusionarse.
- **RF-07** — Si un PR introduce solo alertas por debajo de severidad alta, entonces el check de CodeQL debe pasar y las alertas deben publicarse igualmente.
- **RF-08** — El pipeline debe excluir del análisis de CodeQL el entorno virtual y los artefactos generados (`.venv/`, `__pycache__/`).
- **RF-09** — Mientras el setup avanzado de CodeQL esté activo, el repositorio no debe ejecutar el *default setup* de CodeQL.

### Actualización de dependencias (Dependabot)

- **RF-10** — El pipeline debe revisar una vez por semana si hay versiones nuevas de las dependencias de `uv`, de GitHub Actions y de las imágenes base del `Dockerfile`.
- **RF-11** — El pipeline debe agrupar los updates menores y patch de cada ecosistema en un solo PR de Dependabot.
- **RF-12** — El pipeline debe abrir cada update mayor en su propio PR de Dependabot.
- **RF-13** — Cuando un advisory de seguridad afecte a una dependencia de `uv` o de GitHub Actions, el pipeline debe abrir un PR de seguridad de Dependabot sin esperar la revisión semanal.
- **RF-14** — El pipeline debe titular los commits y PRs de Dependabot con Conventional Commits: `build(deps)` para `uv` y Docker, `ci(deps)` para GitHub Actions.
- **RF-15** — El pipeline debe etiquetar cada PR de Dependabot con `dependencies` y su ecosistema.
- **RF-16** — El pipeline debe mantener las GitHub Actions fijadas por SHA completo de commit al actualizarlas.
- **RF-17** — El pipeline no debe fusionar automáticamente ningún PR de Dependabot.

### Bump de versión en PRs de Dependabot

- **RF-18** — Cuando se abra un PR de Dependabot, el pipeline debe agregarle un commit `chore(release): Bump version to vX.Y.Z` que fije `version` en `pyproject.toml` a la versión de `main` con el patch incrementado en uno.
- **RF-19** — El commit de bump debe cambiar solo `version` en `pyproject.toml` y la versión del proyecto `virtual-assistant` en `uv.lock`, y `uv sync --locked` debe pasar con el resultado.
- **RF-20** — Cuando cambie la versión de `main` mientras un PR de Dependabot está abierto, el pipeline debe actualizar el bump del PR a la nueva versión de `main` más un patch, manteniéndolo en un único commit de bump.
- **RF-21** — Cuando se actualice un PR de Dependabot cuya versión ya sea la de `main` más un patch, el pipeline no debe agregar otro commit.
- **RF-22** — Cuando Dependabot rebase o recree su PR y se pierda el commit de bump, el pipeline debe agregarlo de nuevo como en RF-18.
- **RF-23** — Si un PR no es de Dependabot, entonces el pipeline no debe cambiar su versión ni hacer push a su rama.
- **RF-24** — Si el commit de bump no se puede subir al PR de Dependabot, entonces el pipeline debe fallar un check visible en ese PR y dejar la rama sin cambios.
- **RF-25** — El paso de bump no debe instalar ni ejecutar código del PR de Dependabot mientras tenga un token con permiso de escritura.

### SCA — dependencias

- **RF-26** — Cuando se abra o actualice un PR a `main`, el pipeline debe escanear `uv.lock` en busca de vulnerabilidades conocidas y reportarlo como un check del PR.
- **RF-27** — El pipeline debe escanear `uv.lock` de `main` una vez al día de forma programada.
- **RF-28** — Si el escaneo de `uv.lock` encuentra un hallazgo bloqueante, entonces el check de SCA debe fallar y el PR no debe poder fusionarse.
- **RF-29** — Si el escaneo de `uv.lock` encuentra solo hallazgos sin versión corregida o por debajo de severidad alta, entonces el check de SCA debe pasar y reportarlos.
- **RF-30** — Cuando termine un escaneo de `uv.lock`, el pipeline debe publicar sus hallazgos en el code scanning y listarlos (paquete, versión instalada, versión corregida, severidad, id del advisory) en el resumen del job.

### SCA — imagen y SBOM

- **RF-31** — Cuando `release.yaml` construya la imagen, el pipeline debe escanear la imagen de ambas plataformas en busca de vulnerabilidades de sus paquetes del sistema operativo y de sus dependencias de Python antes de publicarla.
- **RF-32** — Si el escaneo de la imagen encuentra un hallazgo bloqueante, entonces el release no debe publicar ninguna imagen en Docker Hub ni desplegar, y el cluster debe seguir con la versión que está corriendo.
- **RF-33** — Si el escaneo de la imagen encuentra solo hallazgos no bloqueantes, entonces el release debe publicar y desplegar como hoy y reportar los hallazgos en el resumen del job y en el code scanning.
- **RF-34** — Cuando se publique la imagen, el pipeline debe generar su SBOM en un formato estándar (SPDX o CycloneDX) que nombre la imagen, su versión y su digest.
- **RF-35** — El pipeline debe conservar cada SBOM como artefacto descargable de la ejecución del release, nombrado con la imagen y la versión.

### Excepciones

- **RF-36** — Donde un hallazgo esté en el archivo versionado de excepciones con su id de advisory, un motivo y una fecha de vencimiento futura, los checks de SCA deben reportarlo como exceptuado y no contarlo como bloqueante.
- **RF-37** — Si una excepción está vencida, entonces los checks de SCA deben tratar el hallazgo como si no tuviera excepción y nombrar la entrada vencida en el resumen del job.
- **RF-38** — Si una excepción no tiene id de advisory, motivo o fecha de vencimiento, entonces los checks de SCA deben fallar y nombrar la entrada inválida.

### Integración con el pipeline existente

- **RF-39** — `test.yaml` (pyright y pytest) debe seguir ejecutándose y bloqueando los PRs a `main` como hoy, incluidos los PRs de Dependabot.
- **RF-40** — Cuando no haya hallazgos bloqueantes, `release.yaml` debe seguir publicando la imagen con los tags `latest`, versión y `sha-<commit>`, desplegándola por digest y verificando `rollout status` (constitución §10).
- **RF-41** — Cada workflow del pipeline debe declarar los permisos mínimos de token que necesita y fijar las actions de terceros por SHA completo de commit, como los workflows existentes.
- **RF-42** — El pipeline no debe exponer `DOCKERHUB_TOKEN`, `KUBE_CONFIG`, `LOCAL_NETWORK` ni ningún otro secreto a las ejecuciones de PRs de Dependabot ni a los reportes de SCA o CodeQL.

## Fuera de alcance

- Fusión automática de PRs de Dependabot (descartada: cada merge despliega a producción).
- Mantener Dependabot solo con PRs de seguridad o excluir `uv` de los updates semanales (descartado: se activan
  updates semanales en los tres ecosistemas, reemplazando la decisión de la spec 010).
- Bump manual del owner o PRs de Dependabot sin bump (descartados a favor del bump patch automático).
- Escanear la imagen en los PRs o después de publicarla (descartado: solo se escanea en el release, antes de publicar).
- Suite `security-and-quality` de CodeQL o consultas propias.
- Revisión de licencias de las dependencias.
- DAST (escanear la API en ejecución), cambios al secret scanning y escaneo IaC de los manifiestos de Kubernetes.
- Firma de imágenes o atestaciones del SBOM subidas al registro.
- Cualquier cambio en el código, los endpoints, el esquema o el comportamiento de la API.

## Restricciones

- **Aprobación de infra/CI:** workflows nuevos y modificados en `.github/` (CodeQL, SCA, bump), reescritura de
  `.github/dependabot.yml` y cambios en `release.yaml` necesitan la aprobación del owner antes de planificar.
- **Ajustes del repositorio (manual, owner):** desactivar el *default setup* de CodeQL (RF-09); exigir los checks de
  CodeQL, SCA y `test.yaml` en `main` con branch protection o un ruleset (RF-06, RF-28); crear las etiquetas de
  Dependabot; permitir que el workflow de bump haga push a las ramas de Dependabot.
- **Dependencias:** no se espera cambio en `pyproject.toml` ni en `uv.lock` más allá del bump de cierre; las
  herramientas de SCA corren dentro del CI, no como dependencias del proyecto (constitución §1). Si el plan necesita
  una, es una aprobación de dependencia.
- **Riesgo de herramientas:** el plan debe verificar que Dependabot agrupa updates del ecosistema `uv`, que detecta la
  imagen de `COPY --from=ghcr.io/astral-sh/uv` del `Dockerfile` y cómo queda alineada la versión de uv fijada en los
  workflows (`setup-uv` con `version: 0.12.23`).
- **Seguridad del bump (RF-25):** los workflows disparados por PRs de Dependabot con permiso de escritura son una
  superficie de ataque conocida; el plan debe justificar el disparador usado y cómo se actualiza `uv.lock` sin ejecutar
  código del PR.
- **Publicación tras escaneo (RF-31, RF-32):** hoy la imagen se construye y publica en el mismo paso; el plan debe
  separar construcción, escaneo y publicación sin perder el digest multiplataforma que usa el deploy.
- Constitución §6: los secretos del CI siguen en GitHub y nunca llegan a ejecuciones de Dependabot ni a reportes.
- Constitución §9 y §10: SemVer y Conventional Commits; `test.yaml` sigue bloqueando `main`; el deploy sigue fijando
  la imagen por digest.
- Sin cambios en `src/models/` ni migraciones (§5).

## Criterios de finalización

- [ ] Un PR de prueba con un patrón vulnerable deliberado (por ejemplo, SQL armado con texto del request en una rama
      desechable) falla el check de CodeQL; al quitarlo, el check pasa.
- [ ] Un PR de prueba que fija una dependencia en una versión con un advisory crítico/alto con corrección falla el
      check de SCA; una excepción válida lo hace pasar; una excepción vencida o incompleta lo hace fallar de nuevo.
- [ ] Un PR de Dependabot (o uno recreado con `@dependabot recreate`) recibe exactamente un commit
      `chore(release): Bump version to vX.Y.Z` con la versión de `main` más un patch, en `pyproject.toml` y `uv.lock`;
      tras subir la versión de `main`, el bump del PR se actualiza; un PR que no es de Dependabot queda intacto.
- [ ] Las ejecuciones programadas diarias de CodeQL y SCA aparecen en Actions y sus alertas en Security.
- [ ] Una ejecución del release escanea la imagen en ambas plataformas, sube un SBOM como artefacto y sigue
      publicando, desplegando por digest y pasando `rollout status`; un hallazgo bloqueante forzado lo detiene antes
      de publicar.
- [ ] `uv run pytest` y `uv run pyright` en verde; `uv run alembic check` sin diferencias.
- [ ] El *default setup* de CodeQL está desactivado y los checks requeridos están configurados en `main`.
- [ ] README (sección de Dependabot) y `SECURITY.md` describen el nuevo pipeline.
- [ ] Versión subida a `3.10.0` con `uv sync`, en un solo commit `chore(release): Bump version to v3.10.0`.
