from types import SimpleNamespace
from unittest.mock import MagicMock

from ms_mosaic.mq_pub import MqReporter, wait_while_alive


def test_progress_does_not_raise_when_channel_closed():
    ch = MagicMock()
    ch.is_open = True
    ch.basic_publish.side_effect = RuntimeError("Channel is closed.")
    reporter = MqReporter(ch, "job-1", threaded=False)
    reporter.progress("S2_at", 100, 43, "空三完成")


def test_failed_event_does_not_raise_when_channel_closed():
    ch = MagicMock()
    ch.is_open = True
    ch.basic_publish.side_effect = RuntimeError("Channel is closed.")
    reporter = MqReporter(ch, "job-1", threaded=False)
    reporter.event("failed", error="Channel is closed.")


def test_reconnects_and_republishes_after_dead_channel():
    dead = MagicMock()
    dead.is_open = False
    live = MagicMock()
    live.is_open = True
    conn = MagicMock()
    conn.channel.return_value = live

    reporter = MqReporter(
        dead, "job-1", rabbitmq_url="amqp://example", connect=lambda _url: conn, threaded=False
    )
    reporter.progress("S2_at", 100, 43, "空三完成")

    live.basic_publish.assert_called()
    body = live.basic_publish.call_args.kwargs["body"]
    assert b"S2_at" in body
    assert b"job-1" in body


def test_idle_connection_reconnects_before_publish():
    stale = MagicMock()
    stale.is_open = True
    live = MagicMock()
    live.is_open = True
    conn = MagicMock()
    conn.channel.return_value = live
    reporter = MqReporter(
        stale, "job-1", rabbitmq_url="amqp://example", connect=lambda _url: conn, threaded=False
    )
    reporter._last_ok = 1.0
    reporter.progress("S6_report", 0, 95, "质量报告")
    live.basic_publish.assert_called()
    stale.basic_publish.assert_not_called()


def test_threaded_publisher_flushes_on_close():
    live = MagicMock()
    live.is_open = True
    conn = MagicMock()
    conn.channel.return_value = live
    reporter = MqReporter(
        None, "job-1", rabbitmq_url="amqp://example", connect=lambda _url: conn, threaded=True
    )
    reporter.event("succeeded", n_shots=2)
    reporter.close()
    assert any(b"succeeded" in (c.kwargs.get("body") or b"") for c in live.basic_publish.call_args_list)


def test_wait_while_alive_pumps_until_process_exits():
    pumps = []
    proc = SimpleNamespace(alive=True)

    def is_alive():
        return proc.alive

    def join(timeout=None):
        del timeout
        proc.alive = False

    proc.is_alive = is_alive
    proc.join = join
    wait_while_alive(proc, lambda: pumps.append(1), interval=0.01)
    assert pumps == [1]
