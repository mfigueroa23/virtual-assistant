# Constitución — virtual-assistant

Principios innegociables. Toda spec, plan y tarea debe cumplirlos.

1. **Stack fijo:** Python 3.15, FastAPI, SQLAlchemy async + Alembic, LangGraph y PostgreSQL/pgvector, gestionado con `uv`. Agregar dependencias requiere aprobación.
2. **Capas:** `routers` (HTTP) → `services` (lógica) → `models`/`database`; los esquemas van en `interfaces`. Ningún router ejecuta consultas SQL.
3. **Tipado:** `uv run pyright` termina con 0 errores; cada `# pyright: ignore` lleva su justificación.
4. **Tests:** todo cambio de comportamiento incluye un test en `tests/`; `uv run pytest` pasa sin BD real ni llamadas al LLM.
5. **Esquema:** cambiar `src/models/` requiere aprobación y una migración Alembic; `uv run alembic check` no reporta diferencias.
6. **Configuración:** secretos y parámetros entran solo por `Settings` (`src/config.py`); no se versiona `.env` ni se hardcodean credenciales.
7. **Errores:** el cliente recibe mensajes en español y el código HTTP correcto; nunca trazas ni detalles internos.
8. **Idioma:** código en inglés y `snake_case`; comentarios, docs y respuestas en español.
9. **Entrega:** cada spec, al terminar todas sus tareas, sube la versión semver en `pyproject.toml` y ejecuta `uv sync`, en un solo commit junto con `uv.lock`; las tareas intermedias no cambian la versión. El CI (`test.yaml`) debe pasar antes del merge.
10. **Despliegue:** la imagen Docker arranca sin pasos manuales y expone `/health` para las probes de Kubernetes.
