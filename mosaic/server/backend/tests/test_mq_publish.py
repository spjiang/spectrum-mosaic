from unittest.mock import MagicMock

from app.services.mq import MQPublisher


class _FakeCh:
    def __init__(self, publishes):
        self._publishes = publishes

    def queue_declare(self, **_kw):
        return None

    def queue_purge(self, _queue):
        return None

    def basic_publish(self, **kw):
        self._publishes.append(kw)


class _Conn:
    def __init__(self, channel_fn, closed):
        self._channel_fn = channel_fn
        self._closed = closed
        self.is_closed = False
        self.is_open = True

    def channel(self):
        return self._channel_fn()

    def close(self):
        self.is_open = False
        self.is_closed = True
        self._closed.append(True)


def test_publish_job_retries_after_connection_reset(monkeypatch):
    publishes: list[dict] = []
    closed: list[bool] = []
    n = {"i": 0}

    def factory(*_a, **_k):
        n["i"] += 1
        if n["i"] == 1:

            def boom():
                raise ConnectionResetError(104, "Connection reset by peer")

            return _Conn(boom, closed)
        return _Conn(lambda: _FakeCh(publishes), closed)

    monkeypatch.setattr("app.services.mq.pika.BlockingConnection", factory)
    monkeypatch.setattr("app.services.mq.pika.URLParameters", lambda _url: MagicMock())
    mq = MQPublisher("amqp://mosaic:mosaic_secret@rabbitmq:5672/")
    mq.publish_job({"job_id": "1", "action": "start"})
    assert len(publishes) == 1
    assert n["i"] >= 2
    assert closed


def test_publish_closes_connection(monkeypatch):
    publishes: list[dict] = []
    closed: list[bool] = []
    monkeypatch.setattr(
        "app.services.mq.pika.BlockingConnection",
        lambda *_a, **_k: _Conn(lambda: _FakeCh(publishes), closed),
    )
    monkeypatch.setattr("app.services.mq.pika.URLParameters", lambda _url: MagicMock())
    mq = MQPublisher("amqp://mosaic:mosaic_secret@rabbitmq:5672/")
    mq.publish_job({"job_id": "1"})
    mq.publish_job({"job_id": "2"})
    assert len(publishes) == 2
    assert len(closed) == 2
