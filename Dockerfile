# Las imágenes base van fijadas por digest para que Dependabot proponga un PR cuando el tag se mueve, y uv va en su
# propia etapa porque Dependabot solo actualiza las líneas FROM (spec 012, decisión D12 del plan).
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# Stage 1: instala las dependencias de producción en un virtualenv (para la plataforma destino,
# porque asyncpg y greenlet traen binarios compilados). Alpine en las dos etapas: el venv usa las ruedas musllinux de
# uv.lock y protobuf su rueda pura (spec 013, decisiones D1 y D2 del plan). pyyaml, zstandard y websockets no publican
# ruedas para Python 3.15 y se compilan aquí con build-base, que no pasa a la etapa final (spec 014, D5 y D6 del plan).
FROM python:3.15-alpine@sha256:0aa144e9ec2ae8fd80dfd94070ef3e432a0df17b6e5e0091dc05323974ce9913 AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
RUN apk add --no-cache build-base
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY main.py alembic.ini ./
COPY --chmod=755 entrypoint.sh ./
COPY alembic ./alembic
COPY src ./src

# Stage 2: runtime sin privilegios, sin ncurses ni readline y sin gestor de paquetes (RF-10 de la spec 013). Se quitan los
# módulos de Python que los enlazan (y _tkinter, cuyo Tcl/Tk la imagen no trae), las librerías de runtime se recalculan
# con scanelf como en el Dockerfile oficial y apk-tools se borra al final (D3, D4).
FROM python:3.15-alpine@sha256:0aa144e9ec2ae8fd80dfd94070ef3e432a0df17b6e5e0091dc05323974ce9913
RUN cd /usr/local/lib/python3.15/lib-dynload && rm -f _curses*.so readline*.so _tkinter*.so \
  && runDeps="$(scanelf --needed --nobanner --format '%n#p' --recursive /usr/local | tr ',' '\n' | sort -u \
    | awk 'system("[ -e /usr/local/lib/" $1 " ]") == 0 { next } { print "so:" $1 }')" \
  && apk add --no-network --virtual .api-rundeps $runDeps ca-certificates-bundle \
  && apk del --no-network .python-rundeps \
  && adduser -S -D -H app \
  && apk del --no-network apk-tools
WORKDIR /app
COPY --from=build --chown=root:root /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
