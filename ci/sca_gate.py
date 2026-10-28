# Decide si los hallazgos de Grype bloquean un PR o un release (spec 012, decisión D5 del plan).
#
# Un hallazgo bloquea si es Critical o High, tiene versión corregida y ninguna excepción vigente lo cubre. Los que no
# tienen corrección o están por debajo de High solo se reportan. Las excepciones viven en `.github/sca-exceptions.json`:
# `[{"id": "GHSA-…", "reason": "…", "expires": "AAAA-MM-DD"}]`. Una entrada vale hasta su día de vencimiento (UTC)
# inclusive; una vencida deja de cubrir su hallazgo y una incompleta hace fallar el gate. Una excepción coincide con el
# id del advisory o con cualquier id relacionado, así un GHSA cubre el CVE que Grype reporte por él.
#
# La tabla va al resumen del job (`$GITHUB_STEP_SUMMARY`) y a stdout.
#
# Uso: python3 ci/sca_gate.py [--exceptions <archivo>] [--title <texto>] --report <grype.json> …
# `--title` nombra el reporte que le sigue. Sale con 1 ante un hallazgo bloqueante, una excepción inválida o un
# reporte ilegible. Usa solo la biblioteca estándar, así corre con el python3 del runner, sin el venv.
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

BLOCKING_SEVERITIES = {"critical", "high"}
DEFAULT_EXCEPTIONS = ".github/sca-exceptions.json"
STATUS_LABELS = {"blocking": "bloqueante", "excepted": "exceptuado", "reported": "reportado"}


class GateError(Exception):
    pass


@dataclass
class InvalidEntry:
    index: int
    entry: Any
    problem: str


@dataclass
class ExceptionSets:
    valid: list[dict[str, Any]] = field(default_factory = list)
    expired: list[dict[str, Any]] = field(default_factory = list)
    invalid: list[InvalidEntry] = field(default_factory = list)


@dataclass
class Finding:
    package: str
    installed: str
    fixed_in: str
    severity: str
    advisory: str
    status: str


def validate_exceptions(entries: list[Any], today: str) -> ExceptionSets:
    # Separa las entradas en vigentes, vencidas e inválidas (`today` es AAAA-MM-DD).
    sets = ExceptionSets()
    for index, entry in enumerate(entries):
        problem = entry_problem(entry)
        if problem:
            sets.invalid.append(InvalidEntry(index, entry, problem))
        elif entry["expires"] < today:
            sets.expired.append(entry)
        else:
            sets.valid.append(entry)
    return sets


def entry_problem(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return "no es un objeto"
    if not non_empty(entry.get("id")):
        return "falta id"
    if not non_empty(entry.get("reason")):
        return "falta reason"
    if not is_iso_date(entry.get("expires")):
        return "expires no es una fecha AAAA-MM-DD"
    return None


def non_empty(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def is_iso_date(value: Any) -> bool:
    # fromisoformat rechaza días imposibles como 2026-02-30; la regex descarta otros formatos que acepta.
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def classify(match: dict[str, Any], exceptions: list[dict[str, Any]]) -> str:
    # `blocking`, `excepted` o `reported` para un match de Grype, dadas las excepciones vigentes.
    ids = advisory_ids(match)
    if any(entry["id"].strip().lower() in ids for entry in exceptions):
        return "excepted"
    vulnerability = match.get("vulnerability") or {}
    severity = str(vulnerability.get("severity", "")).lower()
    fixed = (vulnerability.get("fix") or {}).get("state") == "fixed"
    return "blocking" if severity in BLOCKING_SEVERITIES and fixed else "reported"


def advisory_ids(match: dict[str, Any]) -> set[str]:
    ids = [(match.get("vulnerability") or {}).get("id")]
    ids += [related.get("id") for related in match.get("relatedVulnerabilities") or []]
    return {advisory.lower() for advisory in ids if advisory}


def findings(report: dict[str, Any], exceptions: list[dict[str, Any]]) -> list[Finding]:
    # Las filas de la tabla de un reporte, una por paquete, versión y advisory.
    rows: dict[str, Finding] = {}
    for match in report["matches"]:
        artifact = match.get("artifact") or {}
        vulnerability = match.get("vulnerability") or {}
        row = Finding(
            package = artifact.get("name", "?"),
            installed = artifact.get("version", "?"),
            fixed_in = ", ".join((vulnerability.get("fix") or {}).get("versions") or []) or "—",
            severity = vulnerability.get("severity", "Unknown"),
            advisory = vulnerability.get("id", "?"),
            status = classify(match, exceptions),
        )
        rows[f"{row.package}@{row.installed}#{row.advisory}"] = row
    return list(rows.values())


def summary(sections: list[tuple[str, list[Finding]]], sets: ExceptionSets) -> str:
    # El resumen en Markdown de cada reporte más las excepciones vencidas e inválidas.
    lines = ["## Análisis de composición de software", ""]
    for title, rows in sections:
        lines += [f"### {title}", ""]
        if not rows:
            lines += ["Sin vulnerabilidades conocidas.", ""]
            continue
        lines.append("| Paquete | Instalada | Corregida en | Severidad | Advisory | Estado |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in rows:
            cells = [row.package, row.installed, row.fixed_in, row.severity, row.advisory, STATUS_LABELS[row.status]]
            lines.append(f"| {' | '.join(cell(value) for value in cells)} |")
        count = {status: sum(row.status == status for row in rows) for status in STATUS_LABELS}
        lines += ["", f"{count['blocking']} bloqueantes, {count['excepted']} exceptuados, {count['reported']} reportados.", ""]
    if sets.expired:
        lines += ["### Excepciones vencidas", ""]
        lines += [f"- {entry['id']} (venció el {entry['expires']}): {entry['reason']}" for entry in sets.expired]
        lines.append("")
    if sets.invalid:
        lines += ["### Excepciones inválidas", ""]
        for invalid in sets.invalid:
            entry_id = invalid.entry.get("id") if isinstance(invalid.entry, dict) else None
            suffix = f" ({entry_id})" if non_empty(entry_id) else ""
            lines.append(f"- #{invalid.index + 1}{suffix}: {invalid.problem}")
        lines.append("")
    return "\n".join(lines)


def cell(value: str) -> str:
    # Las celdas de una tabla Markdown no admiten barras verticales ni saltos de línea.
    return str(value).replace("|", "\\|").replace("\n", " ")


def parse_args(argv: list[str]) -> tuple[str, list[tuple[str, str]]]:
    exceptions = DEFAULT_EXCEPTIONS
    reports: list[tuple[str, str]] = []
    title: str | None = None
    for index in range(0, len(argv), 2):
        flag = argv[index]
        if index + 1 >= len(argv):
            raise GateError(f"{flag} necesita un valor")
        value = argv[index + 1]
        if flag == "--exceptions":
            exceptions = value
        elif flag == "--title":
            title = value
        elif flag == "--report":
            reports.append((title or value, value))
            title = None
        else:
            raise GateError(f"opción desconocida {flag}")
    if not reports:
        raise GateError("hace falta al menos un --report")
    return exceptions, reports


def read_json(file: str, what: str) -> Any:
    try:
        text = Path(file).read_text(encoding = "utf-8")
    except OSError:
        raise GateError(f"no se puede leer el {what} {file}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise GateError(f"el {what} {file} no es JSON válido") from None


def main(argv: list[str]) -> int:
    exceptions_file, reports = parse_args(argv)
    entries = read_json(exceptions_file, "archivo de excepciones") if Path(exceptions_file).exists() else []
    if not isinstance(entries, list):
        raise GateError(f"{exceptions_file} debe contener un arreglo JSON")
    # SCA_GATE_TODAY fija la fecha en los tests.
    today = os.environ.get("SCA_GATE_TODAY") or datetime.now(timezone.utc).date().isoformat()
    sets = validate_exceptions(entries, today)

    sections: list[tuple[str, list[Finding]]] = []
    for title, file in reports:
        report = read_json(file, "reporte")
        if not isinstance(report, dict) or not isinstance(report.get("matches"), list):
            raise GateError(f"el reporte {file} no tiene un arreglo matches")
        sections.append((title, findings(report, sets.valid)))

    text = summary(sections, sets)
    print(text)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding = "utf-8") as file:
            file.write(f"{text}\n")

    blocking = any(row.status == "blocking" for _, rows in sections for row in rows)
    return 1 if blocking or sets.invalid else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GateError as error:
        print(f"sca-gate: {error}", file = sys.stderr)
        sys.exit(1)
