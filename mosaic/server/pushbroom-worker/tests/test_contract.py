"""与后端、前端的约定：步骤编号、队列名、消息格式。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pb_mosaic import mq_pub
from pb_mosaic.__main__ import _parse_bands, main
from pb_mosaic.stages import LABELS, STAGE_IDS, STAGES, global_percent

FRONTEND_LABELS = Path(__file__).resolve().parents[2] / "frontend/src/pages/pushbroom/labels.ts"
BACKEND = Path(__file__).resolve().parents[2] / "backend/app"


def test_stage_weights_sum_to_100() -> None:
    assert sum(w for _, _, w in STAGES) == 100.0
    assert global_percent(STAGE_IDS[0], 0.0) == 0.0
    with pytest.raises(KeyError):
        global_percent("nope", 0.5)


def test_frontend_stage_labels_match() -> None:
    if not FRONTEND_LABELS.is_file():
        pytest.skip("前端源码不在")
    text = FRONTEND_LABELS.read_text(encoding="utf-8")
    pairs = re.findall(r'\{ id: "(\w+)", label: "([^"]+)" \}', text)
    assert pairs == [(sid, LABELS[sid]) for sid in STAGE_IDS]


def test_backend_uses_same_queues() -> None:
    if not BACKEND.is_dir():
        pytest.skip("后端源码不在")
    text = "\n".join(p.read_text(encoding="utf-8") for p in BACKEND.rglob("*.py") if "pushbroom" in p.read_text(encoding="utf-8"))
    for q in (mq_pub.QUEUE_JOBS, mq_pub.QUEUE_CONTROL, mq_pub.QUEUE_PROGRESS, mq_pub.QUEUE_EVENTS):
        assert f'"{q}"' in text, q


def test_reporter_message_shape() -> None:
    sent: list = []

    class Pub:
        def publish(self, q, payload):
            sent.append((q, payload))

    r = mq_pub.MqReporter(Pub(), "id", 3)
    r.progress("P4_mosaic", 0.5, "m")
    r.stage_done("P4_mosaic", 1.234, "done")
    assert sent[0] == (
        mq_pub.QUEUE_PROGRESS,
        {"job_id": "id", "run_attempt": 3, "stage_id": "P4_mosaic", "stage_progress": 50.0, "global_percent": 51.0, "message": "m"},
    )
    assert sent[1][1]["event"] == "stage_done" and sent[1][1]["elapsed_s"] == 1.23
    assert sent[2][1]["stage_progress"] == 100.0


def test_publisher_retries_and_wraps_schema() -> None:
    calls: list = []

    class Ch:
        is_open = True

        def basic_publish(self, **kw):
            if len(calls) == 0:
                calls.append("fail")
                raise ConnectionError("boom")
            calls.append(json.loads(kw["body"]))

    class Conn:
        def channel(self):
            return Ch()

        def close(self):
            pass

        def process_data_events(self, time_limit=0):
            pass

    pub = mq_pub.Publisher("amqp://x", connect=lambda url: Conn())
    pub.publish("q", {"a": 1})
    pub.close()
    assert calls == ["fail", {"schema_version": 1, "a": 1}]


def test_cli_band_parser() -> None:
    assert _parse_bands("1-3, 7，9") == [1, 2, 3, 7, 9]
    with pytest.raises(Exception):
        _parse_bands("5-2")


def test_cli_check_and_run(synth_flight, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["check", "--input", str(synth_flight.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and len(out["strips"]) == 3
    from synth import SENSOR_PARAMS

    code = main(
        ["run", "--input", str(synth_flight.root), "--output", str(tmp_path / "o"), "--bands", "1-2", "--gsd", "0.2",
         "--params", json.dumps(SENSOR_PARAMS)]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert main(["check", "--input", str(tmp_path)]) == 1
