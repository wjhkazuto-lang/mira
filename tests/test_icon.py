import struct
import sys
import zlib

import pytest

from mira.icon import rounded_icon_png
from tests.test_make_app import _png

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="需要 AppKit")


def _alpha(path, x, y) -> float:
    from AppKit import NSBitmapImageRep

    rep = NSBitmapImageRep.imageRepWithData_(path.read_bytes())
    return rep.colorAtX_y_(x, y).alphaComponent()


def test_rounded_icon_shape(tmp_path):
    src, out = tmp_path / "a.png", tmp_path / "r.png"
    _png(src)
    assert rounded_icon_png(src, out) is True
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (1024, 1024)
    assert _alpha(out, 5, 5) == 0  # 四角透明
    assert _alpha(out, 512, 512) == 1  # 中心不透明
    assert _alpha(out, 120, 512) == 1  # 内容区边缘不透明
    assert _alpha(out, 50, 512) == 0  # 留白透明


def test_rounded_icon_false_on_bad_input(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    assert rounded_icon_png(bad, tmp_path / "r.png") is False
    assert rounded_icon_png(tmp_path / "missing.png", tmp_path / "r.png") is False


def test_transparent_source_gets_opaque_background(tmp_path):
    # 右半边全透明的头像：圆角内仍应不透明（垫了底色），四角仍透明
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    n = 64
    row = b"\x00" + (b"\xc2\xa9\x74\xff" * (n // 2) + b"\x00\x00\x00\x00" * (n // 2))
    src, out = tmp_path / "half.png", tmp_path / "r.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(row * n)) + chunk(b"IEND", b""))
    assert rounded_icon_png(src, out) is True
    assert _alpha(out, 880, 512) == 1
    assert _alpha(out, 5, 5) == 0
