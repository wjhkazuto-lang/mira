from datetime import datetime


def now() -> datetime:
    """带时区的本地当前时间。所有模块都通过它（或注入的替身）取时间。"""
    return datetime.now().astimezone()
