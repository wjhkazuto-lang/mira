"""系统通知：只有 Mira.app（bundle 模式）里才真发通知，其他情况一律用空实现。"""

import logging
import uuid
from collections.abc import Callable

log = logging.getLogger(__name__)

BUNDLE_ID = "local.mira.desktop"
_delegate_class = None


class NullNotifier:
    """不发通知：终端模式、关掉通知、或系统通知不可用时用它。"""

    def notify(self, title: str, body: str) -> None:
        pass


def _current_bundle_id() -> str | None:
    from Foundation import NSBundle

    return NSBundle.mainBundle().bundleIdentifier()


def _notification_delegate_class():
    """UNUserNotificationCenter 的 delegate 类（ObjC 类只能定义一次）。"""
    global _delegate_class
    if _delegate_class is None:
        from Foundation import NSObject

        class NotificationDelegate(NSObject):
            on_click = None

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                self, _center, _response, handler
            ):
                try:
                    if self.on_click is not None:
                        from PyObjCTools import AppHelper

                        AppHelper.callAfter(self.on_click)
                except Exception:
                    log.warning("处理通知点击失败", exc_info=True)
                handler()

        _delegate_class = NotificationDelegate
    return _delegate_class


class MacNotifier:
    """pyobjc 的 UserNotifications：首次使用请求一次权限；点通知时回调 on_click。"""

    def __init__(self, on_click: Callable[[], None] | None = None):
        import UserNotifications as UN

        self._UN = UN
        self._center = UN.UNUserNotificationCenter.currentNotificationCenter()
        delegate = _notification_delegate_class().alloc().init()
        delegate.on_click = on_click
        self._delegate = delegate  # 留住引用，不然会被回收
        self._center.setDelegate_(delegate)
        self._center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert, self._on_authorized
        )

    def _on_authorized(self, granted, error) -> None:
        if error is not None:
            log.warning("请求通知权限出错：%s", error)
        elif not granted:
            log.warning("通知权限被拒绝：主动消息只会出现在聊天里")

    def notify(self, title: str, body: str) -> None:
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._send, title, body)  # 统一回主线程再发
        except Exception:
            log.warning("发送系统通知失败", exc_info=True)

    def _send(self, title: str, body: str) -> None:
        try:
            UN = self._UN
            content = UN.UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(body)
            trigger = UN.UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(1.0, False)
            request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
                str(uuid.uuid4()), content, trigger
            )
            self._center.addNotificationRequest_withCompletionHandler_(request, self._on_added)
        except Exception:
            log.warning("发送系统通知失败", exc_info=True)

    def _on_added(self, error) -> None:
        if error is not None:
            log.warning("系统通知发送失败：%s", error)


def make_notifier(*, notifications: bool, fake: bool, on_click: Callable[[], None] | None = None):
    """按环境挑通知器：只有 Mira.app 里（bundle id 对上）才真发系统通知。"""
    if fake or not notifications:
        return NullNotifier()
    try:
        bundle_id = _current_bundle_id()
    except Exception:
        return NullNotifier()
    if bundle_id != BUNDLE_ID:
        log.info("不是 Mira.app（bundle=%s），系统通知不启用", bundle_id)
        return NullNotifier()
    try:
        return MacNotifier(on_click=on_click)
    except Exception:
        log.warning("系统通知不可用，本次改用空实现", exc_info=True)
        return NullNotifier()
