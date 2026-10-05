"""提醒：主动消息发出时让 Dock 图标跳一下、点上一个圆点（不需要任何权限）。

系统通知（UNUserNotificationCenter）需要进程属于一个已签名的 App bundle；
本项目是 launcher 脚本 exec 出来的 python，bundle 身份会丢，走不通，所以用 Dock 提醒。
"""

import logging

log = logging.getLogger(__name__)


class NullNotifier:
    """不提醒：终端模式、关掉提醒、开发模式用它。"""

    def notify(self, title: str, body: str) -> None:
        pass


def _on_main(func) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(func)  # NSApplication 的操作都要在主线程


def _dock_attention() -> None:
    from AppKit import NSApplication, NSInformationalRequest

    app = NSApplication.sharedApplication()
    app.requestUserAttention_(NSInformationalRequest)  # 跳一下
    tile = app.dockTile()
    tile.setShowsApplicationBadge_(True)
    tile.setBadgeLabel_("●")


def _dock_clear_badge() -> None:
    from AppKit import NSApplication

    NSApplication.sharedApplication().dockTile().setBadgeLabel_(None)


class DockNotifier:
    """让 Dock 图标跳到眼前：跳一下 + 一个圆点；窗口回到前台（或你开口）时圆点消失。"""

    def __init__(self):
        self._unread = False

    def notify(self, title: str, body: str) -> None:
        try:
            _on_main(self._apply)
        except Exception:
            log.warning("发送 Dock 提醒失败", exc_info=True)

    def clear(self) -> None:
        if not self._unread:
            return
        self._unread = False
        try:
            _on_main(_dock_clear_badge)
        except Exception:
            log.warning("清除 Dock 圆点失败", exc_info=True)

    def _apply(self) -> None:
        try:
            _dock_attention()
            self._unread = True
        except Exception:
            log.warning("发送 Dock 提醒失败", exc_info=True)


def make_notifier(*, notifications: bool, fake: bool):
    """按环境挑提醒方式：只有真正的桌面 App 里才跳 Dock 图标。"""
    if fake or not notifications:
        return NullNotifier()
    return DockNotifier()
