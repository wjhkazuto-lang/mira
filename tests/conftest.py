import asyncio
import struct
import zlib
from datetime import datetime, timedelta
from pathlib import Path

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


class ManualClock(FakeClock):
    """异步假时钟：sleep() 挂起，直到测试用 advance() 把时间推过唤醒点。
    advance 会按唤醒时间顺序逐个唤醒，所以连续的 sleep（比如逐条发送气泡）时间都是准的。"""

    def __init__(self, start: datetime = START):
        super().__init__(start)
        self._sleepers: list[tuple[datetime, asyncio.Future]] = []

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.t + timedelta(seconds=seconds), fut))
        await fut

    async def advance(self, seconds: float) -> None:
        target = self.t + timedelta(seconds=seconds)
        while True:
            await settle()
            self._sleepers = [s for s in self._sleepers if not s[1].done()]
            due = [s for s in self._sleepers if s[0] <= target]
            if not due:
                break
            wake = min(s[0] for s in due)
            self.t = max(self.t, wake)
            for s in [s for s in self._sleepers if s[0] <= self.t]:
                s[1].set_result(None)
        self.t = target
        await settle()


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
def mclock():
    return ManualClock()


@pytest.fixture
def mstore(mclock):
    return Store(":memory:", now=mclock.now)


def make_png(path: Path, size: int = 64) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    row = b"\x00" + b"\xc2\xa9\x74" * size
    raw = zlib.compress(row * size)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    )
