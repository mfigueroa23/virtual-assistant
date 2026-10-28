import tomllib
from pathlib import Path
import pytest
from ci.dependabot_bump import (
    BumpError, Commit, MergeConflictError, decide, is_bump_commit, next_patch, rebump, set_lock_version,
    set_pyproject_version, trailing_bump)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding = "utf-8")
CURRENT = tomllib.loads(PYPROJECT)["project"]["version"]
LOCK = """version = 1

[[package]]
name = "uvloop"
version = "0.23.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "virtual-assistant"
version = "1.0.0"
source = { virtual = "." }

[[package]]
name = "virtual-assistant-tools"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
"""


def test_u_db1_next_patch_sube_el_numero_de_patch():
    assert next_patch("3.9.0") == "3.9.1"
    assert next_patch("3.9.9") == "3.9.10"
    assert next_patch("10.0.99") == "10.0.100"


def test_u_db2_next_patch_rechaza_lo_que_no_es_x_y_z():
    for version in ("3.9", "v3.9.0", "3.9.0rc1", "3.9.0-rc.1", "3.9.0+build", "", None):
        with pytest.raises(BumpError, match = "no es una versión X.Y.Z"):
            next_patch(version)


def test_u_db3_set_pyproject_version_cambia_solo_la_version_del_pyproject_real():
    updated = set_pyproject_version(PYPROJECT, "9.9.9")
    assert tomllib.loads(updated)["project"]["version"] == "9.9.9"
    assert updated == PYPROJECT.replace(f'version = "{CURRENT}"', 'version = "9.9.9"', 1)


def test_u_db4_set_pyproject_version_ignora_un_version_fuera_de_project():
    text = '[tool.otra]\nversion = "5.0.0"\n\n[project]\nname = "x"\nversion = "1.0.0"\n\n[tool.mas]\nversion = "2.0.0"\n'
    assert set_pyproject_version(text, "1.0.1") == text.replace('version = "1.0.0"', 'version = "1.0.1"')
    with pytest.raises(BumpError, match = r"\[project\]"):
        set_pyproject_version('[project]\nname = "x"\n\n[tool.mas]\nversion = "2.0.0"\n', "1.0.1")


def test_u_db5_set_lock_version_cambia_solo_la_version_del_proyecto():
    updated = set_lock_version(LOCK, "virtual-assistant", "1.0.1")
    expected = LOCK.replace('name = "virtual-assistant"\nversion = "1.0.0"', 'name = "virtual-assistant"\nversion = "1.0.1"')
    assert updated == expected
    assert 'name = "virtual-assistant-tools"\nversion = "1.0.0"' in updated
    # Con el uv.lock real también cambia una sola línea.
    lock = (ROOT / "uv.lock").read_text(encoding = "utf-8")
    real = set_lock_version(lock, "virtual-assistant", "9.9.9")
    assert [a for a, b in zip(lock.splitlines(), real.splitlines()) if a != b] == [f'version = "{CURRENT}"']


def test_u_db6_set_lock_version_falla_si_el_lock_no_tiene_el_proyecto():
    with pytest.raises(BumpError, match = "virtual-assistant"):
        set_lock_version(LOCK.replace('name = "virtual-assistant"\n', 'name = "otro"\n'), "virtual-assistant", "1.0.1")


def test_u_db7_decide_noop_si_el_pr_ya_tiene_main_mas_un_patch():
    assert decide("dependabot[bot]", "3.10.1", "3.10.0") == "noop"


def test_u_db8_decide_rebump_si_main_avanzo():
    assert decide("dependabot[bot]", "3.10.1", "3.10.1") == "rebump"
    assert decide("app/dependabot", "3.10.1", "3.11.0") == "rebump"


def test_u_db9_decide_rebump_si_el_pr_tiene_la_version_de_main():
    assert decide("dependabot[bot]", "3.10.0", "3.10.0") == "rebump"


def test_u_db10_decide_skip_si_el_pr_no_es_de_dependabot():
    for author in ("mfigueroa23", "github-actions[bot]", "portfolio-version-bump[bot]", ""):
        assert decide(author, "3.10.0", "3.10.0") == "skip"


def test_u_db11_is_bump_commit_reconoce_solo_el_asunto_del_bump():
    assert is_bump_commit("chore(release): Bump version to v3.10.1")
    assert not is_bump_commit("chore(release): Version 3.9.0")
    assert not is_bump_commit("chore(release): Bump version to v3.10")
    assert not is_bump_commit("build(deps): Bump the uv-minor-patch group with 3 updates")
    assert not is_bump_commit("chore(release): Bump version to v3.10.1 y algo más")


def pyproject(version: str) -> str:
    return f'[project]\nname = "virtual-assistant"\nversion = "{version}"\n'


def lock(version: str) -> str:
    return f'version = 1\n\n[[package]]\nname = "virtual-assistant"\nversion = "{version}"\nsource = {{ virtual = "." }}\n'


DEP = Commit("dep1", "build(deps): Bump pillow from 13.0.0 to 13.1.0")
BUMP = Commit("bump1", "chore(release): Bump version to v1.0.1")
OWN = Commit("own1", "fix(uv): Ajustando el lock a mano")


class FakeApi:
    # Un GitHub en memoria para una rama de PR: commits, head, archivos por ref y cuánto atrasa a main. Cada escritura
    # queda en `calls`, y `fail` hace que una escritura falle.
    def __init__(self, commits: list[Commit], behind_by: int = 0, files: dict[str, str] | None = None,
                 fail: dict[str, str] | None = None):
        self.commit_list = commits
        self.current = commits[-1].sha
        self.behind = behind_by
        self.files = files or {}
        self.fail = fail or {}
        self.calls: list[tuple] = []

    def head(self) -> str:
        return self.current

    def commits(self) -> list[Commit]:
        return self.commit_list

    def file(self, ref: str, path: str) -> str:
        return self.files.get(f"{ref}:{path}") or (pyproject("1.0.0") if path == "pyproject.toml" else lock("1.0.0"))

    def behind_by(self, sha: str) -> int:
        return self.behind

    def update_ref(self, sha: str) -> None:
        self.calls.append(("update_ref", sha))
        if "update_ref" in self.fail:
            raise BumpError(self.fail["update_ref"])
        self.current = sha

    def merge_main(self) -> str:
        self.calls.append(("merge_main", self.current))
        if "merge_main" in self.fail:
            raise MergeConflictError(self.fail["merge_main"])
        self.current = "merge1"
        return "merge1"

    def create_commit(self, expected_head: str, message: str, files: dict[str, str]) -> str:
        self.calls.append(("create_commit", expected_head, message, files))
        if "create_commit" in self.fail:
            self.current = "otro-push"
            raise BumpError(self.fail["create_commit"])
        self.current = "new1"
        return "new1"


def test_u_db12_trailing_bump_conserva_el_ultimo_commit_que_no_es_bump():
    assert trailing_bump([DEP, BUMP]) == ("dep1", 1)
    assert trailing_bump([DEP]) == ("dep1", 0)
    # Un merge de main que dejó un bump anterior se conserva como cualquier otro commit.
    assert trailing_bump([DEP, Commit("merge1", "Merge branch 'main'"), BUMP]) == ("merge1", 1)
    with pytest.raises(BumpError, match = "después del bump"):
        trailing_bump([DEP, BUMP, OWN])
    with pytest.raises(BumpError, match = "ningún commit de Dependabot"):
        trailing_bump([BUMP])


def test_u_db13_rebump_quita_el_bump_viejo_hace_merge_de_main_y_agrega_un_bump():
    api = FakeApi([DEP, BUMP], behind_by = 2, files = {"merge1:pyproject.toml": pyproject("1.0.1"), "merge1:uv.lock": lock("1.0.1")})
    assert rebump(api, "bump1", "virtual-assistant", "1.0.2") == "new1"
    assert [call[:2] for call in api.calls] == [("update_ref", "dep1"), ("merge_main", "dep1"), ("create_commit", "merge1")]
    _, _, message, files = api.calls[2]
    assert message == "chore(release): Bump version to v1.0.2"
    assert files == {"pyproject.toml": pyproject("1.0.2"), "uv.lock": lock("1.0.2")}


def test_u_db14_rebump_sobre_una_rama_al_dia_solo_agrega_el_bump():
    api = FakeApi([DEP])
    assert rebump(api, "dep1", "virtual-assistant", "1.0.1") == "new1"
    assert [call[:2] for call in api.calls] == [("create_commit", "dep1")]


def test_u_db15_un_conflicto_de_merge_restaura_el_head_original():
    api = FakeApi([DEP, BUMP], behind_by = 1, fail = {"merge_main": "conflicto"})
    with pytest.raises(BumpError, match = "no se puede hacer merge con main"):
        rebump(api, "bump1", "virtual-assistant", "1.0.2")
    assert api.current == "bump1"
    assert [call[0] for call in api.calls] == ["update_ref", "merge_main", "update_ref"]


def test_u_db16_si_la_rama_se_movio_no_escribe_ni_pisa_el_push_de_otro():
    moved = FakeApi([DEP, BUMP])
    with pytest.raises(BumpError, match = "la rama se movió"):
        rebump(moved, "otro-head", "virtual-assistant", "1.0.2")
    assert moved.calls == []

    raced = FakeApi([DEP], fail = {"create_commit": 'Expected branch to point to "dep1"'})
    with pytest.raises(BumpError, match = "la rama se movió"):
        rebump(raced, "dep1", "virtual-assistant", "1.0.1")
    assert raced.current == "otro-push"
    assert [call[0] for call in raced.calls] == ["create_commit"]


def test_u_db17_un_bump_seguido_de_otro_commit_nunca_se_resetea():
    api = FakeApi([DEP, BUMP, OWN])
    with pytest.raises(BumpError, match = "después del bump"):
        rebump(api, "own1", "virtual-assistant", "1.0.2")
    assert api.calls == []
