import socket

from mira.__main__ import port_in_use


def test_port_in_use_detects_listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        assert port_in_use("127.0.0.1", port) is True
    finally:
        s.close()
    assert port_in_use("127.0.0.1", port) is False
