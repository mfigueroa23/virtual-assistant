import re
import sys
import tomllib
from pathlib import Path

# Spec 014, RF-05: desarrollo, CI e imagen corren con Python 3.15, la versión que trae la corrección de las CVEs del
# intérprete (D3 del plan).
ROOT = Path(__file__).parent.parent


def test_rf05_el_interprete_es_python_3_15():
    assert sys.version_info[:2] == (3, 15)


def test_rf05_python_version_y_requires_python_fijan_3_15():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert (ROOT / ".python-version").read_text().strip() == "3.15"
    assert project["requires-python"] == ">=3.15"


def test_d7_el_uv_del_ci_es_el_de_la_imagen():
    # uv 0.12.23 solo conoce 3.15.0rc3: el CI usa el mismo uv que la etapa `uv` del Dockerfile (D7 del plan 014).
    image_uv = re.search(r"astral-sh/uv:(\S+?)@", (ROOT / "Dockerfile").read_text())
    assert image_uv
    for workflow in ("test.yaml", "release.yaml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text()
        assert re.findall(r"setup-uv@\S+.*\n\s+with:\n\s+version: (\S+)", text) == [image_uv.group(1)]


def test_rf06_rf08_la_imagen_es_python_3_15_alpine_sin_compilador_en_la_etapa_final():
    # La etapa build compila pyyaml, zstandard y websockets; el compilador no llega a la final (D5 y D6 del plan 014).
    stages = re.split(r"^FROM ", (ROOT / "Dockerfile").read_text(), flags = re.MULTILINE)[1:]
    python_stages = [stage for stage in stages if stage.startswith("python:")]
    assert len(python_stages) == 2
    assert all(re.match(r"python:3\.15-alpine@sha256:[0-9a-f]{64}", stage) for stage in python_stages)
    build, final = python_stages
    assert "build-base" in build and "build-base" not in final
    assert "/usr/local/lib/python3.15/lib-dynload" in final
