import struct
import sys

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
