# Las imágenes base van fijadas por digest para que Dependabot proponga un PR cuando el tag se mueve, y uv va en su
# propia etapa porque Dependabot solo actualiza las líneas FROM (spec 012, decisión D12 del plan).
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# Stage 1: instala las dependencias de producción en un virtualenv (para la plataforma destino,
# porque asyncpg y greenlet traen binarios compilados).
FROM python:3.14-slim@sha256:82abc9f53600280b0757126f83c5a7d47088592d2acb6b4c0b9e97f76865508c AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY main.py alembic.ini ./
COPY --chmod=755 entrypoint.sh ./
COPY alembic ./alembic
COPY src ./src

# Stage 2: runtime sin privilegios.
FROM python:3.14-slim@sha256:82abc9f53600280b0757126f83c5a7d47088592d2acb6b4c0b9e97f76865508c
RUN useradd --system --no-create-home app
WORKDIR /app
COPY --from=build --chown=root:root /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
