from pathlib import Path

from app.services.paths import trial_dirs

DB = Path(__file__).resolve().parents[2] / "db"


def test_trial_dirs_for_seed_users():
    assert trial_dirs("trial1") == ("/data/input/trial1", "/data/output/runs/trial1")
    assert trial_dirs("trial2") == ("/data/input/trial2", "/data/output/runs/trial2")


def test_db_dir_contains_init_files():
    assert (DB / "init.sql").is_file()
    assert (DB / "init-data.sql").is_file()
