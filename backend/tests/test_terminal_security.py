import pytest
from starlette.websockets import WebSocketDisconnect

from backend.config import settings
from backend.services.terminal_access import (
    MAX_PENDING_TERMINAL_TICKETS,
    TERMINAL_PROTOCOL_PREFIX,
    TerminalTicketStore,
    origin_matches,
    terminal_ticket_store,
    trusted_local_peer,
)


@pytest.fixture(autouse=True)
def clear_terminal_tickets():
    terminal_ticket_store.clear()
    yield
    terminal_ticket_store.clear()


def test_terminal_session_rejects_cross_site_origin(client, monkeypatch):
    monkeypatch.setattr("backend.main._is_local_request", lambda request: True)
    response = client.post(
        "/api/terminal/session",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "终端访问来源不受信任",
        "code": "TERMINAL_FORBIDDEN",
    }


def test_terminal_session_requires_origin(client, monkeypatch):
    monkeypatch.setattr("backend.main._is_local_request", lambda request: True)

    response = client.post("/api/terminal/session")

    assert response.status_code == 403


def test_terminal_session_is_short_lived_and_not_cacheable(client, monkeypatch):
    monkeypatch.setattr("backend.main._is_local_request", lambda request: True)

    response = client.post(
        "/api/terminal/session",
        headers={"Origin": settings.frontend_origin},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["expires_in"] == 30
    assert response.json()["ticket"] in terminal_ticket_store


def test_terminal_origin_parser_rejects_malformed_port():
    assert not origin_matches("http://localhost:not-a-port", settings.frontend_origin)


def test_terminal_origin_parser_normalizes_default_port():
    assert origin_matches("http://localhost:80", "http://localhost")
    assert origin_matches("https://reader.example", "https://reader.example:443")
    assert not origin_matches("http://reader.example", "https://reader.example")


@pytest.mark.parametrize(
    ("client_host", "forwarded_for", "expected"),
    [
        ("127.0.0.1", None, True),
        ("127.0.0.1", "8.8.8.8", False),
        ("172.18.0.2", "172.18.0.1", False),
        ("172.18.0.2", "127.0.0.1, 192.168.1.10", False),
        ("172.18.0.2", None, False),
        ("172.18.0.2", "8.8.8.8", False),
        ("8.8.8.8", "127.0.0.1", False),
        ("127.0.0.1", "127.0.0.1", True),
    ],
)
def test_terminal_peer_must_be_direct_or_fully_local(client_host, forwarded_for, expected):
    assert trusted_local_peer(client_host, forwarded_for) is expected


def test_terminal_websocket_rejects_missing_session(client, monkeypatch):
    monkeypatch.setattr("backend.main._is_localhost", lambda websocket: True)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/terminal/ws",
            headers={"Origin": settings.frontend_origin},
        ):
            pass

    assert exc_info.value.code == 4003


def test_terminal_session_ticket_is_one_time(client, monkeypatch):
    monkeypatch.setattr("backend.main._is_local_request", lambda request: True)
    monkeypatch.setattr("backend.main._is_localhost", lambda websocket: True)
    response = client.post(
        "/api/terminal/session",
        headers={"Origin": settings.frontend_origin},
    )
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    protocol = f"{TERMINAL_PROTOCOL_PREFIX}{ticket}"

    async def close_immediately(websocket, cwd):
        return None

    monkeypatch.setattr("backend.services.terminal.handle_terminal_websocket", close_immediately)
    with client.websocket_connect(
        "/api/terminal/ws",
        headers={"Origin": settings.frontend_origin},
        subprotocols=[protocol],
    ) as websocket:
        assert websocket.accepted_subprotocol == protocol

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/terminal/ws",
            headers={"Origin": settings.frontend_origin},
            subprotocols=[protocol],
        ):
            pass

    assert exc_info.value.code == 4003


def test_terminal_websocket_rejects_cross_site_origin_with_valid_ticket(client, monkeypatch):
    monkeypatch.setattr("backend.main._is_localhost", lambda websocket: True)
    ticket = terminal_ticket_store.issue()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/terminal/ws",
            headers={"Origin": "https://attacker.example"},
            subprotocols=[f"{TERMINAL_PROTOCOL_PREFIX}{ticket}"],
        ):
            pass

    assert exc_info.value.code == 4003
    assert ticket in terminal_ticket_store


def test_terminal_ticket_store_expires_and_consumes_once():
    now = 100.0
    store = TerminalTicketStore(clock=lambda: now)
    ticket = store.issue()
    protocol = f"{TERMINAL_PROTOCOL_PREFIX}{ticket}"

    assert store.consume_protocol(protocol) == protocol
    assert store.consume_protocol(protocol) is None

    expired_ticket = store.issue()
    now += 31
    assert store.consume_protocol(f"{TERMINAL_PROTOCOL_PREFIX}{expired_ticket}") is None


def test_terminal_ticket_store_is_bounded():
    store = TerminalTicketStore(max_pending=MAX_PENDING_TERMINAL_TICKETS)
    for _ in range(MAX_PENDING_TERMINAL_TICKETS + 5):
        store.issue()

    assert len(store) == MAX_PENDING_TERMINAL_TICKETS


@pytest.mark.asyncio
async def test_terminal_handler_receives_ping_resize_text_and_bytes(monkeypatch):
    from backend.services import terminal

    class FakeSession:
        instances = []

        def __init__(self, cwd):
            self.cwd = cwd
            self.is_alive = True
            self.writes = []
            self.resizes = []
            self.closed = False
            self.instances.append(self)

        async def spawn(self):
            return None

        def read(self):
            return b""

        def write(self, data):
            self.writes.append(data)

        def resize(self, cols, rows):
            self.resizes.append((cols, rows))

        def close(self):
            self.is_alive = False
            self.closed = True

    class FakeWebSocket:
        def __init__(self):
            self.messages = iter([
                {"type": "websocket.receive", "text": "ping"},
                {"type": "websocket.receive", "text": "\x1b[8;40;120t"},
                {"type": "websocket.receive", "text": "pwd\n"},
                {"type": "websocket.receive", "bytes": b"ls\n"},
                {"type": "websocket.disconnect", "code": 1000},
            ])
            self.sent_text = []

        async def receive(self):
            return next(self.messages)

        async def send_text(self, value):
            self.sent_text.append(value)

        async def send_bytes(self, value):
            return None

    monkeypatch.setattr(terminal, "TerminalSession", FakeSession)
    websocket = FakeWebSocket()

    await terminal.handle_terminal_websocket(websocket, "/tmp")

    session = FakeSession.instances[0]
    assert websocket.sent_text == ["pong"]
    assert session.resizes == [(120, 40)]
    assert session.writes == [b"pwd\n", b"ls\n"]
    assert session.closed
