from __future__ import annotations

from functools import lru_cache

from app.services.mq import FakeMQPublisher, MQPublisher


@lru_cache
def get_mq() -> MQPublisher:
    return MQPublisher()


def get_mq_override():
    return get_mq()
