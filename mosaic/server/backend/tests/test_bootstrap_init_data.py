import os
from pathlib import Path

from app.services import paths
from app.services.paths import ensure_worker_tree, trial_dirs

DB = Path(__file__).resolve().parents[2] / "db"


def test_trial_dirs_for_seed_users():
    assert trial_dirs("trial1") == ("/data/input/trial1", "/data/output/runs/trial1")
    assert trial_dirs("trial2") == ("/data/input/trial2", "/data/output/runs/trial2")


def test_db_dir_contains_init_files():
    assert (DB / "init.sql").is_file()
    assert (DB / "init-data.sql").is_file()


def test_ensure_worker_tree_owns_children_not_stop(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    root.chmod(0o700)
    leaf = root / "output" / "runs" / "trial1"
    ensure_worker_tree(leaf, stop=root, uid=os.getuid(), gid=os.getgid())
    assert leaf.stat().st_mode & 0o777 == 0o755
    assert (root / "output" / "runs").stat().st_mode & 0o777 == 0o755
    assert leaf.stat().st_uid == os.getuid()
    assert root.stat().st_mode & 0o777 == 0o700


def test_ensure_worker_tree_chmod_when_owner_stays_root(tmp_path: Path, monkeypatch):
    def noop_chown(_path, _uid, _gid):
        return None

    monkeypatch.setattr("app.services.paths.os.chown", noop_chown)
    root = tmp_path / "data"
    root.mkdir()
    leaf = root / "output" / "runs" / "1530" / "log"
    ensure_worker_tree(leaf, stop=root, uid=10001, gid=10001)
    assert leaf.stat().st_uid != 10001
    assert leaf.stat().st_mode & 0o777 == 0o777
    assert (root / "output").stat().st_mode & 0o777 == 0o777


def test_ensure_worker_tree_falls_back_when_chown_denied(tmp_path: Path, monkeypatch):
    def deny(_path, _uid, _gid):
        raise PermissionError("denied")

    monkeypatch.setattr(paths.os, "chown", deny)
    root = tmp_path / "data"
    root.mkdir()
    root.chmod(0o755)
    leaf = root / "input" / "trial1"
    ensure_worker_tree(leaf, stop=root)
    assert leaf.stat().st_mode & 0o777 == 0o777
    assert (root / "input").stat().st_mode & 0o777 == 0o777
    assert root.stat().st_mode & 0o777 == 0o755
