# Política de seguridad

## Versiones soportadas

| Versión | Soportada |
|---|---|
| 3.x.x | Sí |
| 2.x.x | No |
| 1.x.x | No |

## Reportar una vulnerabilidad

No abras un issue público para reportar una vulnerabilidad.

Escribe al equipo de mantenimiento a **[correo de seguridad]** e incluye:

- Descripción del problema y su impacto.
- Pasos para reproducirlo.
- Versión o commit afectado.

Confirmaremos la recepción y te mantendremos informado mientras se corrige.

## Consideraciones de seguridad del proyecto

### Configuración en la tabla `property`

Toda la configuración de la aplicación, salvo la conexión a la base de datos y `LOG_LEVEL`, se guarda en la tabla `property` **en texto plano**. Cualquier persona con acceso de lectura a la base de datos, a sus backups o a sus logs de consultas puede ver esos valores, incluidos los secretos que se guarden ahí (por ejemplo, API keys).

Para reducir el riesgo:

- Limita el acceso de lectura a la tabla `property` a los usuarios que lo necesiten.
- Usa un usuario de base de datos propio para la aplicación, con los permisos mínimos.
- Protege y cifra los backups de la base de datos.
- Rota cualquier secreto que se haya expuesto.

### Archivo `.env`

- El `.env` contiene las credenciales de la base de datos y **no debe subirse al repositorio** (ya está en `.gitignore`).
- Usa `.env.example` como plantilla, sin valores reales.

### Análisis de seguridad en CI

- **CodeQL** analiza el código Python y los workflows con la suite `security-extended` en cada PR a `main`, en cada push a `main` y una vez al día. Una alerta nueva de severidad alta o crítica bloquea el merge.
- **SCA:** Grype escanea `uv.lock` en cada PR y una vez al día, y la imagen Docker (`amd64` y `arm64`) en cada release, antes de publicarla. Un hallazgo crítico o alto que ya tiene versión corregida bloquea el PR o detiene el release sin publicar ni desplegar; los demás solo se reportan.
- **Imagen:** `python:3.15-alpine` fijada por digest, sin compilador, ncurses, readline ni gestor de paquetes (`apk-tools`), y la API corre con un usuario sin privilegios.
- **SBOM:** cada imagen publicada deja un SBOM SPDX por plataforma como artefacto de la ejecución del release.
- **Dependabot** propone cada semana las versiones nuevas de las dependencias de Python, las GitHub Actions y las imágenes base, y abre de inmediato los PRs de seguridad. Ninguno se fusiona solo.

La pestaña **Security → Code scanning** muestra todas las alertas de CodeQL y solo los hallazgos de Grype con versión corregida (`--only-fixed`); el resumen de cada ejecución lista todos los hallazgos, con y sin corrección. Una excepción se pide en un PR que agregue la entrada a `.github/sca-exceptions.json` con el id del advisory, el motivo y una fecha de vencimiento (ver la sección CI/CD del README).

### Logs

- No registres secretos ni valores de properties sensibles en los logs.
- En producción usa `LOG_LEVEL=INFO` o superior; el nivel `DEBUG` puede mostrar información interna.
