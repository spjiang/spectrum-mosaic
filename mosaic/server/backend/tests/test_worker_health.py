from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path
import time

from app.services.mq import check_worker
from app.services.cli import check_program_dir
from app.config import Settings


def test_check_worker_ok(monkeypatch):
    declared = SimpleNamespace(method=SimpleNamespace(consumer_count=1))
    conn = MagicMock()
    conn.channel.return_value.queue_declare.return_value = declared
    monkeypatch.setattr("app.services.mq.pika.BlockingConnection", lambda *_a, **_k: conn)
    monkeypatch.setattr("app.services.mq.pika.URLParameters", lambda url: MagicMock())
    assert check_worker("amqp://mosaic:mosaic_secret@127.0.0.1:5672/") == "ok"
    conn.close.assert_called()


def test_check_worker_missing(monkeypatch):
    declared = SimpleNamespace(method=SimpleNamespace(consumer_count=0))
    conn = MagicMock()
    conn.channel.return_value.queue_declare.return_value = declared
    monkeypatch.setattr("app.services.mq.pika.BlockingConnection", lambda *_a, **_k: conn)
    monkeypatch.setattr("app.services.mq.pika.URLParameters", lambda url: MagicMock())
    assert "没有 Worker" in check_worker("amqp://mosaic:mosaic_secret@127.0.0.1:5672/")


def test_check_worker_timeout(monkeypatch):
    def hang(_url: str) -> str:
        time.sleep(2)
        return "ok"

    monkeypatch.setattr("app.services.mq._probe_worker", hang)
    monkeypatch.setattr("app.services.mq._PROBE_TIMEOUT_S", 0.05)
    assert "超时" in check_worker("amqp://mosaic:mosaic_secret@127.0.0.1:5672/")


def test_check_program_dir_ok(tmp_path: Path):
    pkg = tmp_path / "ms_mosaic"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    settings = Settings(cli_cwd=str(tmp_path), cli_python="python")
    status, cfg = check_program_dir(settings)
    assert status == "ok"
    assert cfg["cwd"] == str(tmp_path)


def test_check_program_dir_missing(tmp_path: Path):
    settings = Settings(cli_cwd=str(tmp_path / "nope"), cli_python="python")
    status, _cfg = check_program_dir(settings)
    assert status.startswith("error:")
