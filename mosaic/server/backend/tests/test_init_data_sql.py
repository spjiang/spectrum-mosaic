from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "db"


def test_init_data_sql_exists_and_has_profiles():
    text = (DB / "init-data.sql").read_text(encoding="utf-8")
    assert "标准全波段" in text
    assert "RGB 快速预览" in text
    assert "trial1" in text and "trial2" in text
    assert "rbac_menus" in text
    assert "ON CONFLICT" in text or "WHERE NOT EXISTS" in text


def test_init_sql_is_ddl_only():
    text = (DB / "init.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE" in text
    assert "expires_at" in text
    assert "INSERT INTO" not in text
