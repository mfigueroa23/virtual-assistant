import re
from pathlib import Path

# Con QEMU, compilar zstandard para arm64 llevaba el build del release de ~8 a ~25 minutos (3.11.0). Cada plataforma se
# construye en su runner nativo y el job package solo arma el índice, escanea y publica.
RELEASE = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yaml").read_text()


def job(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", RELEASE, flags = re.MULTILINE | re.DOTALL)
    assert match, f"falta el job {name}"
    return match.group(1)


def test_el_release_no_emula_plataformas_con_qemu():
    assert "setup-qemu-action" not in RELEASE


def test_cada_plataforma_se_construye_en_su_runner_nativo():
    build = job("build")
    assert re.search(r"- arch: amd64\n\s+runner: ubuntu-24\.04\n", build)
    assert re.search(r"- arch: arm64\n\s+runner: ubuntu-24\.04-arm\n", build)
    assert "runs-on: ${{ matrix.runner }}" in build
    assert "platforms: linux/${{ matrix.arch }}" in build


def test_el_build_no_publica_y_package_escanea_el_indice_armado():
    build, package = job("build"), job("package")
    assert "push: true" not in build and "type=oci" in build
    assert "needs: build" in package and "build-push-action" not in package
    assert "imagetools create" in package and "Scan Image" in package
