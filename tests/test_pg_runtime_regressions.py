"""Regression coverage for PostgreSQL compilation and event-loop pool waits."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from src import projection
from src.mcp import rule_tools
from src.models import UserAccountProjection
from src.routers import ws


def test_postgres_upsert_compiles_with_native_conflict_clause():
    db = MagicMock()
    db.get_bind.return_value.dialect = postgresql.dialect()
    projection._upsert(db, UserAccountProjection, ('user_id', 'sync_id'),
                       {'user_id': 'user', 'sync_id': 'account', 'name': 'Account'})
    statement = db.execute.call_args.args[0]
    assert 'ON CONFLICT' in str(statement.compile(dialect=postgresql.dialect()))


def _check_pool_wait(make_operation, make_session, monkeypatch, module):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    session = make_session(entered, release)
    session.__enter__ = lambda self: self
    session.__exit__ = lambda self, *args: closed.set()
    monkeypatch.setattr(module, 'SessionLocal', session)
    timer = threading.Timer(5, release.set)
    timer.start()

    async def run():
        task = asyncio.create_task(make_operation())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert not release.is_set(), 'Database checkout blocked the event loop'
        finally:
            release.set()
            await task
    try:
        asyncio.run(run())
        assert closed.is_set()
    finally:
        release.set()
        timer.cancel()


def test_websocket_database_wait_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr(ws, 'decode_token', lambda _: {
        'type': 'access', 'scopes': [ws.SCOPE_WEB_WRITE], 'sub': 'user'})

    class Socket:
        app = SimpleNamespace(state=SimpleNamespace(ws_manager=MagicMock()))
        async def close(self, **kwargs):
            pass

    def session(entered, release):
        class WaitingSession:
            def scalar(self, statement):
                entered.set()
                assert release.wait(6)
                return None
        return WaitingSession
    _check_pool_wait(lambda: ws.websocket_endpoint(Socket(), 'test'), session, monkeypatch, ws)


def test_recurring_scan_database_wait_does_not_block_event_loop(monkeypatch):
    def session(entered, release):
        class WaitingSession:
            def scalars(self, query):
                entered.set()
                assert release.wait(6)
                return SimpleNamespace(all=lambda: [])
        return WaitingSession
    _check_pool_wait(rule_tools.execute_due_rules, session, monkeypatch, rule_tools.wt)
