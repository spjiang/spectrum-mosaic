from pathlib import Path
from types import SimpleNamespace

from app.services.release_artifacts import list_artifacts, resolve_artifact_file


def test_list_artifacts_marks_missing(tmp_path: Path):
    worker = tmp_path / "worker-image.tar"
    worker.write_bytes(b"abc")
    row = SimpleNamespace(version="0.1.0", arch="amd64", artifact_path=str(worker))
    items = list_artifacts(row)
    by_name = {i["name"]: i for i in items}
    assert by_name["worker"]["available"] is True
    assert by_name["worker"]["size_bytes"] == 3
    assert by_name["backend"]["available"] is False
    assert resolve_artifact_file(row, "worker") == worker
    assert resolve_artifact_file(row, "postgres") is None
    assert resolve_artifact_file(row, "nope") is None


def test_pushbroom_artifact_listed(tmp_path: Path):
    worker = tmp_path / "worker-image.tar"
    worker.write_bytes(b"abc")
    pb = tmp_path / "pushbroom-worker-image.tar"
    pb.write_bytes(b"abcd")
    row = SimpleNamespace(version="0.2.0", arch="arm64", artifact_path=str(worker))
    by_name = {i["name"]: i for i in list_artifacts(row)}
    assert by_name["pushbroom_worker"]["available"] is True
    assert by_name["pushbroom_worker"]["size_bytes"] == 4
    assert resolve_artifact_file(row, "pushbroom_worker") == pb
