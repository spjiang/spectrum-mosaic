"""增量收片：命名、传完判定、入库时不覆盖已有文件。"""

from pathlib import Path

from app.services.incremental import _copy_into_dataset, inbox_dir_for, next_arrival, parse_capture_name


def test_parse_capture_name_accepts_new_exposure_and_new_band():
    assert parse_capture_name("MAX_0008_Color_D.jpg") == (8, "Color", "D")
    assert parse_capture_name("MAX_0008_510nm_D.tif") == (8, "510nm", "D")
    assert parse_capture_name("MAX_0012_1200nm_R.tif") == (12, "1200nm", "R")
    assert parse_capture_name("notes.txt") is None


def test_file_is_ready_only_after_size_stays_the_same():
    assert next_arrival(None, 100) == "incoming"
    assert next_arrival(("incoming", 100), 100) == "ready"
    assert next_arrival(("incoming", 100), 180) == "incoming"
    assert next_arrival(("ready", 180), 180) == "ready"
    assert next_arrival(("ready", 180), 200) == "incoming"
    assert next_arrival(("ingested", 180), 180) == "ingested"


def test_inbox_sits_beside_the_dataset_dir():
    assert inbox_dir_for("/data/input/flight_a") == "/data/input/flight_a.inbox"


def test_copy_does_not_replace_a_different_file(tmp_path: Path):
    src = tmp_path / "inbox" / "MAX_0001_450nm_D.tif"
    dest = tmp_path / "data" / "MAX_0001_450nm_D.tif"
    src.parent.mkdir()
    dest.parent.mkdir()
    src.write_bytes(b"new-bytes")
    dest.write_bytes(b"old")
    assert _copy_into_dataset(src, dest) == "conflict"
    assert dest.read_bytes() == b"old"
    assert src.is_file()


def test_copy_writes_when_destination_is_missing(tmp_path: Path):
    src = tmp_path / "MAX_0001_Color_D.jpg"
    dest = tmp_path / "out" / "MAX_0001_Color_D.jpg"
    src.write_bytes(b"rgb")
    assert _copy_into_dataset(src, dest) == "copied"
    assert dest.read_bytes() == b"rgb"


def test_latest_marker_is_the_highest_shot_index():
    from datetime import datetime, timedelta, timezone

    from app.services.incremental import latest_marker

    base = datetime(2026, 10, 30, tzinfo=timezone.utc)
    marker = latest_marker(
        [
            (2, "MAX_0002_Color_D.jpg", base + timedelta(minutes=5)),
            (5, "MAX_0005_450nm_R.tif", base),
            (5, "MAX_0005_850nm_R.tif", base + timedelta(minutes=1)),
        ]
    )
    assert marker == (5, "MAX_0005_850nm_R.tif", base + timedelta(minutes=1))
    assert latest_marker([]) is None


def test_new_files_rerun_the_same_job_instead_of_creating_another():
    from datetime import datetime, timedelta, timezone

    from app.services.incremental import mosaic_decision

    started = datetime(2026, 10, 30, tzinfo=timezone.utc)
    later = started + timedelta(minutes=5)
    assert mosaic_decision("running", started, [later], manual=False) == "wait"
    assert mosaic_decision("running", started, [later], manual=True) == "wait"
    assert mosaic_decision("succeeded", started, [later], manual=False) == "rerun"
    assert mosaic_decision("succeeded", started, [started], manual=False) == "skip"
    assert mosaic_decision("succeeded", started, [started], manual=True) == "rerun"
    assert mosaic_decision(None, None, [started], manual=False) == "create"
    assert mosaic_decision(None, None, [], manual=False) == "skip"
    assert mosaic_decision("queued", None, [later], manual=True) == "wait"
