import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from ci.sca_gate import classify, findings, summary, validate_exceptions

SCRIPT = Path(__file__).resolve().parent.parent / "ci" / "sca_gate.py"
TODAY = "2026-10-28"


def match(
    id: str = "GHSA-aaaa-bbbb-cccc", severity: str = "High", state: str = "fixed", versions: list[str] | None = None,
    related: list[str] | None = None, name: str = "pillow", version: str = "12.3.0",
) -> dict[str, Any]:
    # Un match de Grype con los campos que lee el gate.
    return {
        "vulnerability": {"id": id, "severity": severity, "fix": {"state": state, "versions": ["13.0.0"] if versions is None else versions}},
        "relatedVulnerabilities": [{"id": related_id} for related_id in related or []],
        "artifact": {"name": name, "version": version},
    }


def exception(**changes: Any) -> dict[str, Any]:
    return {"id": "GHSA-aaaa-bbbb-cccc", "reason": "Solo se usa al leer adjuntos internos", "expires": "2026-12-31"} | changes


def run_cli(tmp_path: Path, reports: list[Any], exceptions: list[Any] | None = None) -> subprocess.CompletedProcess[str]:
    # Ejecuta el CLI con los reportes y excepciones escritos en tmp_path; None deja un reporte sin crear.
    exceptions_file = tmp_path / "exceptions.json"
    exceptions_file.write_text(json.dumps(exceptions or []))
    args = [sys.executable, str(SCRIPT), "--exceptions", str(exceptions_file)]
    for index, report in enumerate(reports):
        file = tmp_path / f"report-{index}.json"
        if report is not None:
            file.write_text(report if isinstance(report, str) else json.dumps(report))
        args += ["--title", f"Reporte {index}", "--report", str(file)]
    env = {key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"} | {"SCA_GATE_TODAY": TODAY}
    return subprocess.run(args, capture_output = True, text = True, env = env)


def test_u_sg1_un_high_con_correccion_bloquea_y_el_cli_sale_con_1(tmp_path):
    assert classify(match(), []) == "blocking"
    assert classify(match(severity = "Critical"), []) == "blocking"
    result = run_cli(tmp_path, [{"matches": [match()]}])
    assert result.returncode == 1
    assert "1 bloqueantes" in result.stdout


def test_u_sg2_un_critical_sin_correccion_solo_se_reporta(tmp_path):
    sin_correccion = match(severity = "Critical", state = "not-fixed", versions = [])
    assert classify(sin_correccion, []) == "reported"
    assert classify(match(severity = "High", state = "wont-fix", versions = []), []) == "reported"
    assert run_cli(tmp_path, [{"matches": [sin_correccion]}]).returncode == 0


def test_u_sg3_un_medium_con_correccion_solo_se_reporta(tmp_path):
    assert classify(match(severity = "Medium"), []) == "reported"
    assert run_cli(tmp_path, [{"matches": [match(severity = "Medium")]}]).returncode == 0


def test_u_sg4_un_reporte_sin_matches_pasa_y_lo_dice(tmp_path):
    result = run_cli(tmp_path, [{"matches": []}])
    assert result.returncode == 0
    assert "Sin vulnerabilidades conocidas" in result.stdout


def test_u_sg5_una_excepcion_valida_deja_el_hallazgo_exceptuado(tmp_path):
    assert classify(match(), [exception()]) == "excepted"
    result = run_cli(tmp_path, [{"matches": [match()]}], [exception()])
    assert result.returncode == 0
    assert "exceptuado" in result.stdout


def test_u_sg6_una_excepcion_de_un_ghsa_cubre_el_cve_relacionado():
    cve = match(id = "CVE-2026-1234", related = ["GHSA-aaaa-bbbb-cccc"])
    assert classify(cve, [exception()]) == "excepted"
    assert classify(cve, [exception(id = "cve-2026-1234")]) == "excepted"
    assert classify(cve, [exception(id = "GHSA-zzzz-zzzz-zzzz")]) == "blocking"


def test_u_sg7_una_excepcion_vencida_ayer_no_cubre_y_una_que_vence_hoy_si(tmp_path):
    sets = validate_exceptions([exception(expires = "2026-10-27"), exception(id = "GHSA-hoy", expires = TODAY)], TODAY)
    assert [entry["id"] for entry in sets.expired] == ["GHSA-aaaa-bbbb-cccc"]
    assert [entry["id"] for entry in sets.valid] == ["GHSA-hoy"]
    assert sets.invalid == []

    result = run_cli(tmp_path, [{"matches": [match()]}], [exception(expires = "2026-10-27")])
    assert result.returncode == 1
    assert "Excepciones vencidas" in result.stdout
    assert "GHSA-aaaa-bbbb-cccc" in result.stdout


def test_u_sg8_una_entrada_sin_motivo_es_invalida_y_falla_sin_hallazgos(tmp_path):
    for reason in (None, "", "   "):
        sets = validate_exceptions([exception(reason = reason)], TODAY)
        assert len(sets.invalid) == 1
        assert "reason" in sets.invalid[0].problem

    result = run_cli(tmp_path, [{"matches": []}], [exception(reason = "")])
    assert result.returncode == 1
    assert "Excepciones inválidas" in result.stdout
    assert "#1 (GHSA-aaaa-bbbb-cccc)" in result.stdout


def test_u_sg9_una_entrada_sin_id_o_con_fecha_que_no_es_iso_es_invalida():
    sets = validate_exceptions(
        [exception(id = None), exception(expires = "31/12/2026"), exception(expires = "2026-02-30"), "GHSA-no-es-objeto"], TODAY)
    assert [entry.problem for entry in sets.invalid] == [
        "falta id", "expires no es una fecha AAAA-MM-DD", "expires no es una fecha AAAA-MM-DD", "no es un objeto"]


def test_u_sg10_una_fila_por_hallazgo_con_todas_las_columnas_y_una_seccion_por_reporte(tmp_path):
    # El mismo advisory reportado dos veces por Grype es una sola fila.
    rows = findings({"matches": [match(), match(), match(id = "GHSA-low", severity = "Low", name = "a|b", versions = [])]}, [])
    assert len(rows) == 2
    text = summary([("Lockfile", rows), ("Imagen", [])], validate_exceptions([], TODAY))
    assert "| Paquete | Instalada | Corregida en | Severidad | Advisory | Estado |" in text
    assert "| pillow | 12.3.0 | 13.0.0 | High | GHSA-aaaa-bbbb-cccc | bloqueante |" in text
    assert "| a\\|b | 12.3.0 | — | Low | GHSA-low | reportado |" in text
    assert "### Lockfile" in text
    assert "### Imagen\n\nSin vulnerabilidades conocidas" in text

    result = run_cli(tmp_path, [{"matches": [match()]}, {"matches": []}])
    assert "### Reporte 0" in result.stdout
    assert "### Reporte 1" in result.stdout


def test_u_sg11_un_reporte_que_falta_o_no_es_valido_sale_con_1_y_un_error_de_una_linea(tmp_path):
    for report in (None, "{ no es json", '{"matches": 3}'):
        result = run_cli(tmp_path, [report])
        assert result.returncode == 1
        assert result.stderr.startswith("sca-gate: ")
        assert result.stderr.count("\n") == 1
        assert "Traceback" not in result.stderr
