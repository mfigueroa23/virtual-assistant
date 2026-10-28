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
