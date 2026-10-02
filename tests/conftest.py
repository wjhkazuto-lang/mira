from datetime import datetime, timedelta

import pytest

from mira.store import Store

START = datetime.fromisoformat("2026-10-02T21:00:00+08:00")


class FakeClock:
    """同步假时钟：now() 返回当前时间，advance() 往前推。"""

    def __init__(self, start: datetime = START):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock):
    return Store(":memory:", now=clock.now)
