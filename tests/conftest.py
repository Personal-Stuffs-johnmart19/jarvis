"""Offline tests must not send input to the user's desktop or external services."""

import pytest


@pytest.fixture(autouse=True)
def prevent_real_global_keyboard_input(monkeypatch):
    import keyboard

    def blocked_global_input(*args, **kwargs):
        pytest.fail(
            "A test attempted global input. Use an explicit keyboard mock.",
            pytrace=False,
        )

    for name in ("press_and_release", "send", "press", "release", "write", "play"):
        monkeypatch.setattr(keyboard, name, blocked_global_input)


@pytest.fixture(autouse=True)
def prevent_paid_or_external_network_calls(monkeypatch):
    """Unit/regression tests use mocks; real API checks are explicit benchmarks only."""
    import socket
    original = socket.socket.connect

    def connect(connection, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            pytest.fail("A test attempted external networking. Use a mock; live APIs require an explicit benchmark.", pytrace=False)
        return original(connection, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
