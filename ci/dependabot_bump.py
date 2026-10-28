# Mantiene cada PR abierto de Dependabot una versión patch por encima de `main` (spec 012, RF-18 a RF-25).
#
# Por cada PR abierto de Dependabot (o solo `--pr N`) compara la versión de `pyproject.toml` del PR con la de `main`.
# Si no es `main` + 1 patch, reconstruye la punta de la rama con la API de GitHub, así cada commit queda firmado y
# verificado (decisión D11 del plan):
#   1. quita los commits de bump del final moviendo la rama al último commit que no es bump, sin tocar los de Dependabot;
#   2. si la rama está atrasada, hace merge de `main` (un merge commit que crea GitHub);
#   3. agrega un commit `chore(release): Bump version to vX.Y.Z` con `pyproject.toml` y `uv.lock` (createCommitOnBranch).
# Cada escritura espera el head que leyó, así nunca pisa un push concurrente. Si un paso falla, la rama vuelve a su head
# original (si nadie hizo push mientras tanto), queda un commit status `Version bump` fallido y un comentario pide
# `@dependabot recreate`.
#
# Solo llama a `gh`: no hace checkout, ni instala, ni ejecuta nada del PR (D1); los dos archivos se editan como texto
# (D6). Usa solo la biblioteca estándar, así corre con el python3 del runner.
#
# Entorno: GH_TOKEN (token de la App del bump) y GITHUB_REPOSITORY (owner/repo).
#
# Uso: python3 ci/dependabot_bump.py [--pr <número>]
import base64
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from typing import Any, Protocol

STATUS_CONTEXT = "Version bump"
DEPENDABOT_LOGINS = {"dependabot[bot]", "app/dependabot", "dependabot"}
BUMP_SUBJECT = re.compile(r"chore\(release\): Bump version to v\d+\.\d+\.\d+")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
VERSION_LINE = re.compile(r'^(version = ")[^"]*(")$', re.MULTILINE)
TABLE_HEADER = re.compile(r"^\[", re.MULTILINE)
BUMPED_FILES = ("pyproject.toml", "uv.lock")


class BumpError(Exception):
    pass


class MergeConflictError(BumpError):
    pass


@dataclass(frozen = True)
class Commit:
    sha: str
    subject: str


class BumpApi(Protocol):
    # Las llamadas a GitHub que necesita `rebump`, para la rama de un PR.
    def head(self) -> str: ...
    def commits(self) -> list[Commit]: ...
    def file(self, ref: str, path: str) -> str: ...
    def behind_by(self, sha: str) -> int: ...
    def update_ref(self, sha: str) -> None: ...
    def merge_main(self) -> str: ...
    def create_commit(self, expected_head: str, message: str, files: dict[str, str]) -> str: ...


def next_patch(version: Any) -> str:
    # `X.Y.Z` → `X.Y.(Z+1)`.
    parts = VERSION.fullmatch(version) if isinstance(version, str) else None
    if not parts:
        raise BumpError(f"{json.dumps(version)} no es una versión X.Y.Z")
    return f"{parts[1]}.{parts[2]}.{int(parts[3]) + 1}"


def project_field(pyproject_text: str, key: str) -> str:
    return tomllib.loads(pyproject_text)["project"][key]


def set_pyproject_version(text: str, version: str) -> str:
    # El texto de pyproject.toml con solo el valor de `version` de la tabla `[project]` reemplazado.
    header = re.search(r"^\[project\][ \t]*$", text, re.MULTILINE)
    if not header:
        raise BumpError("pyproject.toml no tiene la tabla [project]")
    following = TABLE_HEADER.search(text, header.end())
    end = following.start() if following else len(text)
    table, count = VERSION_LINE.subn(rf"\g<1>{version}\g<2>", text[header.end():end], count = 1)
    if count == 0:
        raise BumpError("la tabla [project] de pyproject.toml no tiene una línea version")
    return text[:header.end()] + table + text[end:]


def set_lock_version(text: str, name: str, version: str) -> str:
    # El texto de uv.lock con solo la versión del paquete `name` (el proyecto) reemplazada.
    pattern = re.compile(rf'^(name = "{re.escape(name)}"\nversion = ")[^"]*(")$', re.MULTILINE)
    updated, count = pattern.subn(rf"\g<1>{version}\g<2>", text, count = 1)
    if count == 0:
        raise BumpError(f"uv.lock no tiene el paquete {name}")
    return updated


def is_bump_commit(subject: str) -> bool:
    return BUMP_SUBJECT.fullmatch(subject) is not None


def decide(author: str, pr_version: str, main_version: str) -> str:
    # `skip` (no es de Dependabot), `noop` (ya está en main + 1 patch) o `rebump`.
    if author not in DEPENDABOT_LOGINS:
        return "skip"
    return "noop" if pr_version == next_patch(main_version) else "rebump"


def trailing_bump(commits: list[Commit]) -> tuple[str, int]:
    # El commit al que vuelve la rama (el último que no es bump) y cuántos bumps del final quita. Un bump con otros
    # commits después nunca se quita, porque se llevaría esos commits.
    index = len(commits) - 1
    while index >= 0 and is_bump_commit(commits[index].subject):
        index -= 1
    if index < 0:
        raise BumpError("la rama no tiene ningún commit de Dependabot que conservar")
    if any(is_bump_commit(commit.subject) for commit in commits[:index]):
        raise BumpError("alguien agregó un commit después del bump; queda para el owner")
    return commits[index].sha, len(commits) - 1 - index


def rebump(api: BumpApi, head_sha: str, project: str, version: str) -> str:
    # Reconstruye la punta de la rama con un único bump a `version`; `head_sha` es el head que leyó quien llama.
    # Devuelve el head nuevo.
    def moved() -> BumpError:
        return BumpError("la rama se movió durante el bump; queda para la próxima ejecución")

    if api.head() != head_sha:
        raise moved()
    commits = api.commits()
    if not commits or commits[-1].sha != head_sha:
        raise moved()
    keep, _ = trailing_bump(commits)

    current = head_sha
    try:
        if keep != current:
            api.update_ref(keep)
            current = keep
        if api.behind_by(current) > 0:
            try:
                current = api.merge_main()
            except MergeConflictError:
                raise BumpError("no se puede hacer merge con main") from None
        files = {
            "pyproject.toml": set_pyproject_version(api.file(current, "pyproject.toml"), version),
            "uv.lock": set_lock_version(api.file(current, "uv.lock"), project, version),
        }
        return api.create_commit(current, f"chore(release): Bump version to v{version}", files)
    except BumpError:
        # Deshace solo lo que hizo esta ejecución: si otro hizo push mientras tanto, su head se queda.
        if api.head() != current:
            raise moved() from None
        if current != head_sha:
            api.update_ref(head_sha)
        raise


def gh(*args: str, input: str | None = None) -> str:
    try:
        result = subprocess.run(["gh", *args], input = input, capture_output = True, text = True, check = True)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip().splitlines()
        raise BumpError(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: {detail[0] if detail else error.returncode}") from None
    return result.stdout


class GithubApi:
    # Las llamadas de BumpApi con `gh`, para la rama de un PR.
    def __init__(self, repo: str, number: int, branch: str):
        self.repo = repo
        self.number = number
        self.branch = branch

    def head(self) -> str:
        return gh("api", f"repos/{self.repo}/git/ref/heads/{self.branch}", "--jq", ".object.sha").strip()

    def commits(self) -> list[Commit]:
        lines = gh("api", "--paginate", f"repos/{self.repo}/pulls/{self.number}/commits",
                   "--jq", '.[] | {sha, subject: (.commit.message | split("\n")[0])}').splitlines()
        return [Commit(**json.loads(line)) for line in lines if line]

    def file(self, ref: str, path: str) -> str:
        # El media type raw devuelve el archivo tal cual, también cuando pasa de 1 MB.
        return gh("api", "-H", "Accept: application/vnd.github.raw+json", f"repos/{self.repo}/contents/{path}?ref={ref}")

    def behind_by(self, sha: str) -> int:
        return int(gh("api", f"repos/{self.repo}/compare/main...{sha}", "--jq", ".behind_by").strip())

    def update_ref(self, sha: str) -> None:
        gh("api", "-X", "PATCH", f"repos/{self.repo}/git/refs/heads/{self.branch}", "-f", f"sha={sha}", "-F", "force=true")

    def merge_main(self) -> str:
        try:
            sha = gh("api", f"repos/{self.repo}/merges", "-f", f"base={self.branch}", "-f", "head=main",
                     "-f", f"commit_message=Merge branch 'main' into {self.branch}", "--jq", ".sha").strip()
        except BumpError as error:
            if "409" in str(error):
                raise MergeConflictError("conflicto de merge") from None
            raise
        # 204: no había nada que fusionar.
        return sha or self.head()

    def create_commit(self, expected_head: str, message: str, files: dict[str, str]) -> str:
        additions = [{"path": path, "contents": base64.b64encode(text.encode("utf-8")).decode("ascii")} for path, text in files.items()]
        body = {
            "query": "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
            "variables": {"input": {
                "branch": {"repositoryNameWithOwner": self.repo, "branchName": self.branch},
                "expectedHeadOid": expected_head,
                "message": {"headline": message},
                "fileChanges": {"additions": additions},
            }},
        }
        return gh("api", "graphql", "--input", "-", "--jq", ".data.createCommitOnBranch.commit.oid", input = json.dumps(body)).strip()


def open_pull_requests(repo: str, number: int | None) -> list[dict[str, Any]]:
    fields = "number,headRefName,headRefOid,author,state,isCrossRepository"
    if number is not None:
        pulls = [json.loads(gh("pr", "view", str(number), "--repo", repo, "--json", fields))]
    else:
        pulls = json.loads(gh("pr", "list", "--repo", repo, "--author", "app/dependabot", "--state", "open",
                              "--base", "main", "--limit", "100", "--json", fields))
    return [pull for pull in pulls if pull["state"] == "OPEN" and not pull["isCrossRepository"]]


def status_of(repo: str, sha: str) -> str | None:
    statuses = json.loads(gh("api", f"repos/{repo}/commits/{sha}/status")).get("statuses") or []
    return next((status["state"] for status in statuses if status["context"] == STATUS_CONTEXT), None)


def set_status(repo: str, sha: str, state: str, description: str) -> None:
    gh("api", f"repos/{repo}/statuses/{sha}", "-f", f"state={state}", "-f", f"context={STATUS_CONTEXT}",
       "-f", f"description={description}")


def process_pull_request(repo: str, pull: dict[str, Any]) -> str:
    number = pull["number"]
    api = GithubApi(repo, number, pull["headRefName"])
    head = api.head()
    if head != pull["headRefOid"]:
        return f"#{number}: la rama se movió al leerla, queda para la próxima ejecución"

    main_pyproject = api.file("main", "pyproject.toml")
    main_version = project_field(main_pyproject, "version")
    pr_version = project_field(api.file(head, "pyproject.toml"), "version")
    action = decide(pull["author"]["login"], pr_version, main_version)
    if action == "skip":
        return f"#{number}: no es de Dependabot, se omite"

    version = next_patch(main_version)
    if action == "noop":
        if status_of(repo, head) != "success":
            set_status(repo, head, "success", f"Versión {version}")
        return f"#{number}: ya está en {version}"

    try:
        pushed = rebump(api, head, project_field(main_pyproject, "name"), version)
    except BumpError as error:
        message = str(error).splitlines()[0]
        # Un status fallido y un comentario por head, no uno por ejecución.
        if status_of(repo, head) != "failure":
            set_status(repo, head, "failure", f"No se pudo subir a {version}: {message}"[:140])
            gh("pr", "comment", str(number), "--repo", repo, "--body",
               f"No se pudo aplicar el bump a `v{version}`: {message}. Comenta `@dependabot recreate` para reconstruir "
               "este PR sobre `main`; el bump se agrega de nuevo después.")
        raise BumpError(f"#{number}: {message}") from None
    set_status(repo, pushed, "success", f"Versión {version}")
    return f"#{number}: {pr_version} → {version}"


def main(argv: list[str]) -> int:
    number: int | None = None
    if argv:
        if len(argv) != 2 or argv[0] != "--pr" or not argv[1].isdigit():
            raise BumpError("uso: dependabot_bump.py [--pr <número>]")
        number = int(argv[1])
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not os.environ.get("GH_TOKEN") or not repo:
        raise BumpError("hacen falta GH_TOKEN y GITHUB_REPOSITORY")

    pulls = open_pull_requests(repo, number)
    print(f"{len(pulls)} PR(s) de Dependabot abiertos")
    failures = 0
    for pull in pulls:
        try:
            print(process_pull_request(repo, pull))
        except BumpError as error:
            failures += 1
            print(str(error).splitlines()[0], file = sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BumpError as error:
        print(f"dependabot-bump: {str(error).splitlines()[0]}", file = sys.stderr)
        sys.exit(1)
