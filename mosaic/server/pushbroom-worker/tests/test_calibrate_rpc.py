"""标定请求在 worker 里执行，失败时回错误而不是抛出。"""

from pb_mosaic.calibrate_rpc import execute


def test_execute_returns_cli_json():
    seen = {}

    def run(dirs, timeout):
        seen["dirs"] = dirs
        seen["timeout"] = timeout
        return {"combined": {"usable": True}}

    out = execute(["/data/input/pushbroom/A"], run=run)
    assert out["combined"]["usable"] is True
    assert seen["dirs"] == ["/data/input/pushbroom/A"]
    assert seen["timeout"] == 120


def test_execute_turns_timeout_and_bad_count_into_errors():
    def run(_dirs, _timeout):
        raise TimeoutError("超时")

    assert "超时" in execute(["/data/a"], run=run)["error"]
    assert "1～8" in execute([], run=run)["error"]
    assert "1～8" in execute([str(i) for i in range(9)], run=run)["error"]
