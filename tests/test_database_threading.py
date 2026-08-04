"""Poll-cursor persistence must survive being called from a worker thread.

Regression guard for the 2026-07-27 outage: the poll loop runs
`poller.poll()` via `asyncio.to_thread`, so `Database.set_cursor` executes on
a worker thread while the connection was created on the main thread. With the
default `check_same_thread=True` that raised sqlite3.ProgrammingError on every
cursor write, silently defeating the restart-safety cursor persistence this
branch (fix/missed-exit-on-restart) exists to provide.

Run: ./venv/bin/python -m pytest tests/test_database_threading.py -v
"""

import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from storage.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "thread_test.db"))
    yield database
    database.close()


def test_set_cursor_from_a_worker_thread(db):
    """The exact failure mode: write the cursor from a DIFFERENT thread than
    the one that opened the connection."""
    result = {}

    def worker():
        try:
            db.set_cursor("chan_1", "msg_100")
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 — we want to surface it
            result["error"] = exc

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert "error" not in result, f"cross-thread write raised: {result.get('error')!r}"
    assert result.get("ok") is True
    # And the value is actually persisted, readable from the main thread.
    assert db.get_cursor("chan_1") == "msg_100"


def test_set_cursor_via_asyncio_to_thread(db):
    """Mirror production exactly: main.py drives poll() through
    asyncio.to_thread, which is where the cursor write really happens."""

    async def drive():
        await asyncio.to_thread(db.set_cursor, "chan_2", "msg_200")

    asyncio.run(drive())
    assert db.get_cursor("chan_2") == "msg_200"


def test_cursor_upsert_updates_in_place(db):
    db.set_cursor("chan_3", "msg_1")
    db.set_cursor("chan_3", "msg_2")
    assert db.get_cursor("chan_3") == "msg_2"


def test_cursor_survives_reopen(tmp_path):
    """The whole point: a fresh Database (i.e. a restart) resumes from the
    persisted cursor instead of re-seeding to latest."""
    path = str(tmp_path / "persist.db")
    first = Database(path)
    first.set_cursor("chan_4", "msg_persisted")
    first.close()

    second = Database(path)
    try:
        assert second.get_cursor("chan_4") == "msg_persisted"
    finally:
        second.close()


def test_concurrent_writes_from_many_threads(db):
    """Serialized-mode sanity: many worker threads writing at once must not
    corrupt or raise."""
    errors = []

    def worker(n):
        try:
            for i in range(20):
                db.set_cursor(f"chan_{n}", f"msg_{n}_{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    for n in range(8):
        assert db.get_cursor(f"chan_{n}") == f"msg_{n}_19"
