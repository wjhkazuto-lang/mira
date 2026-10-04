"""把头像做成 macOS 风格的圆角方形图标（四周留白、透明底）。"""

from pathlib import Path


def rounded_icon_png(src: Path, out: Path, size: int = 1024) -> bool:
    """按 Apple 图标网格：内容占 80%，圆角约 22.37%。任何失败都返回 False。"""
    try:
        from AppKit import (
            NSBezierPath,
            NSBitmapImageRep,
            NSColor,
            NSCompositingOperationSourceOver,
            NSGraphicsContext,
            NSImage,
            NSMakeRect,
            NSPNGFileType,
        )

        _BG = NSColor.colorWithSRGBRed_green_blue_alpha_(0xF5 / 255, 0xF2 / 255, 0xE9 / 255, 1.0)
        img = NSImage.alloc().initWithContentsOfFile_(str(src))
        if img is None:
            return False
        w, h = img.size().width, img.size().height
        if w <= 0 or h <= 0:
            return False
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, size, size, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
        if rep is None:
            return False
        body = size * 0.8
        inset = (size - body) / 2
        radius = body * 0.2237
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        if ctx is None:  # 拿不到画布就别写出一张空白图
            return False
        NSGraphicsContext.saveGraphicsState()
        try:
            NSGraphicsContext.setCurrentContext_(ctx)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(inset, inset, body, body), radius, radius)
            path.addClip()
            _BG.setFill()  # 先垫米白底（style.css 的 --bg），头像透明处才不会露洞
            path.fill()
            side = min(w, h)  # 居中裁成正方形（aspect-fill）
            crop = NSMakeRect((w - side) / 2, (h - side) / 2, side, side)
            img.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(inset, inset, body, body), crop, NSCompositingOperationSourceOver, 1.0)
        finally:
            NSGraphicsContext.restoreGraphicsState()
        data = rep.representationUsingType_properties_(NSPNGFileType, None)
        if data is None:
            return False
        out.write_bytes(bytes(data))
        return out.stat().st_size > 0
    except Exception:
        return False
