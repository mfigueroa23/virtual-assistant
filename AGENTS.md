# AGENTS.md — virtual-assistant

## Proyecto
API de asistente virtual escrito con python, empaqueado en docker
y desplegado en kubernetes.

## Comandos
- Ejecutar dev mode: `uv run fastapi dev`
- Tests: `uv run pytest`
- Crear migracion: `uv run alembic revision --autogenerate -m <"mensaje">`
- Verificar migraciones: `uv run alembic check`
- Aplicar migracion: `uv run alembic upgrade head`
- Lint: `uv run pyright`

## Estilo y convenciones
- Python 3.15
- Variables y fuciones en `snake_case`
- Codigo en ingles
- Comentarios y respuestas al cliente en español
- Documentacion en español

## Reglas
- Lee docs/constitution.md y la spec activa antes de tocar código.
- No agregar dependencias sin aprobacion.
- No modificar los modelos de base de datos sin aprobacion.
- No modificar archivos en `./docs/` sin aprobacion 

## Al terminar cualquier tarea
- Ejecutar los test y lint
- Verficar que las migraciones esten aplicadas
- No subir la version ni regenerar `uv.lock` en tareas intermedias de una spec

## Al terminar todas las tareas de una spec
- Aplicar cambio de version segun convencion en `pyproject.toml` y aplicar `uv sync`
- El cambio de version y el `uv.lock` regenerado van en un solo commit: `chore(release): Bump version to vX.Y.Z`
