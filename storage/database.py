"""SQLite database — trades, positions, and message log."""

import functools
import json
import sqlite3
import threading
from datetime import datetime, timezone


def _synchronized(method):
    """Serialize a DB method under the instance lock.

    One connection is shared across the asyncio loop thread and
    asyncio.to_thread worker threads (the poll loop writes cursors from a
    worker; trades/positions are written from the loop). Python's sqlite3 in
    serialized mode won't corrupt data, but two threads interleaving
    execute()+commit() on the SAME connection clash on its shared transaction
    state ("cannot start a transaction within a transaction"). This lock makes
    each public DB operation atomic w.r.t. the others. Write volume is tiny
    (one poll cycle / ~15s), so contention is negligible.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Database:
    TRADE_COLUMNS = frozenset({
        'analyst', 'message_id', 'action', 'asset_type', 'ticker', 'direction',
        'strike', 'expiry', 'entry_price', 'executed_price', 'quantity',
        'position_size', 'trim_fraction', 'pnl', 'confidence', 'raw_message',
        'created_at', 'executed_at', 'status',
    })
    POSITION_COLUMNS = frozenset({
        'analyst', 'ticker', 'asset_type', 'direction', 'strike', 'expiry',
        'entry_price', 'current_quantity', 'original_quantity', 'position_size',
        'trim_count', 'status', 'opened_at', 'closed_at', 'total_pnl',
        'stop_price', 'target_prices',
    })

    def __init__(self, db_path: str = "trading_bot.db"):
        self.db_path = db_path
        # Guards every DB method (see _synchronized). RLock so a synchronized
        # method can safely call another without self-deadlock. Must exist
        # before init_db() touches the connection.
        self._lock = threading.RLock()
        # check_same_thread=False: this single connection is shared across the
        # asyncio loop thread and asyncio.to_thread worker threads (the poll
        # loop persists cursors from a worker via set_cursor — see
        # discord_poller.poll / main.py:_poll_cycle). Safe here because Python's
        # sqlite3 is built in serialized mode (sqlite3.threadsafety == 3), so
        # the library serializes access internally; the default same-thread
        # guard would otherwise raise ProgrammingError on the cross-thread
        # cursor write. WAL keeps readers from blocking the writer.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.init_db()

    def init_db(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyst TEXT NOT NULL,
                message_id TEXT UNIQUE NOT NULL,
                action TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                ticker TEXT NOT NULL,
                direction TEXT,
                strike REAL,
                expiry TEXT,
                entry_price REAL,
                executed_price REAL,
                quantity INTEGER,
                position_size REAL,
                trim_fraction REAL,
                pnl REAL,
                confidence REAL,
                raw_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                executed_at TIMESTAMP,
                status TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyst TEXT NOT NULL,
                ticker TEXT NOT NULL,
                asset_type TEXT,
                direction TEXT,
                strike REAL,
                expiry TEXT,
                entry_price REAL,
                current_quantity INTEGER,
                original_quantity INTEGER,
                position_size REAL,
                trim_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'open',
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                total_pnl REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS message_log (
                message_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                content TEXT,
                parsed_as TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS poll_cursor (
                channel_id TEXT PRIMARY KEY,
                last_message_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Safe column migrations
        for col, col_type in [("asset_type", "TEXT"), ("stop_price", "REAL"), ("target_prices", "TEXT")]:
            try:
                cur.execute(f"ALTER TABLE positions ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column already exists
        
        self.conn.commit()

    # ── message_log CRUD ─────────────────────────────────────────

    @_synchronized
    def log_message(self, message_id: str, channel_id: str, content: str,
                    parsed_as: dict | None = None):
        self.conn.execute(
            "INSERT OR IGNORE INTO message_log (message_id, channel_id, content, parsed_as) "
            "VALUES (?, ?, ?, ?)",
            (message_id, channel_id, content,
             json.dumps(parsed_as) if parsed_as else None),
        )
        self.conn.commit()

    @_synchronized
    def is_message_processed(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM message_log WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    # ── poll cursor (survives restart — LIVE_SAFETY.md Blocker 1) ──

    @_synchronized
    def get_cursor(self, channel_id: str) -> str | None:
        """Last-seen message id for a channel, or None if never polled."""
        row = self.conn.execute(
            "SELECT last_message_id FROM poll_cursor WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return row[0] if row else None

    @_synchronized
    def set_cursor(self, channel_id: str, message_id: str):
        """Persist the poll cursor so a restart resumes from here instead of
        reseeding to latest (which would skip anything posted while down)."""
        self.conn.execute(
            "INSERT INTO poll_cursor (channel_id, last_message_id, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(channel_id) DO UPDATE SET "
            "last_message_id = excluded.last_message_id, updated_at = CURRENT_TIMESTAMP",
            (channel_id, message_id),
        )
        self.conn.commit()

    # ── trades CRUD ──────────────────────────────────────────────

    @_synchronized
    def create_trade(self, *, analyst: str, message_id: str, action: str,
                     asset_type: str, ticker: str, direction: str | None = None,
                     strike: float | None = None, expiry: str | None = None,
                     entry_price: float | None = None,
                     executed_price: float | None = None,
                     quantity: int | None = None,
                     position_size: float | None = None,
                     trim_fraction: float | None = None,
                     pnl: float | None = None,
                     confidence: float | None = None,
                     raw_message: str | None = None,
                     status: str = "pending") -> int:
        cur = self.conn.execute(
            "INSERT INTO trades "
            "(analyst, message_id, action, asset_type, ticker, direction, "
            "strike, expiry, entry_price, executed_price, quantity, position_size, "
            "trim_fraction, pnl, confidence, raw_message, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (analyst, message_id, action, asset_type, ticker, direction,
             strike, expiry, entry_price, executed_price, quantity, position_size,
             trim_fraction, pnl, confidence, raw_message, status),
        )
        self.conn.commit()
        return cur.lastrowid

    @_synchronized
    def update_trade(self, trade_id: int, **kwargs):
        if not kwargs:
            return
        bad = set(kwargs) - self.TRADE_COLUMNS
        if bad:
            raise ValueError(f"Invalid trade column(s): {bad}")
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [trade_id]
        self.conn.execute(f"UPDATE trades SET {cols} WHERE id = ?", vals)
        self.conn.commit()

    # ── positions CRUD ───────────────────────────────────────────

    @_synchronized
    def open_position(self, *, analyst: str, ticker: str,
                      direction: str | None = None,
                      asset_type: str | None = None,
                      strike: float | None = None,
                      expiry: str | None = None,
                      entry_price: float | None = None,
                      current_quantity: int | None = None,
                      original_quantity: int | None = None,
                      position_size: float | None = None,
                      stop_price: float | None = None,
                      target_prices: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO positions "
            "(analyst, ticker, direction, asset_type, strike, expiry, entry_price, "
            "current_quantity, original_quantity, position_size, stop_price, target_prices) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (analyst, ticker, direction, asset_type or 'option', strike, expiry, entry_price,
             current_quantity, original_quantity, position_size, stop_price, target_prices),
        )
        self.conn.commit()
        return cur.lastrowid

    @_synchronized
    def update_position(self, position_id: int, **kwargs):
        if not kwargs:
            return
        bad = set(kwargs) - self.POSITION_COLUMNS
        if bad:
            raise ValueError(f"Invalid position column(s): {bad}")
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [position_id]
        self.conn.execute(f"UPDATE positions SET {cols} WHERE id = ?", vals)
        self.conn.commit()

    @_synchronized
    def close_position(self, position_id: int, total_pnl: float = 0):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE positions SET status = 'closed', closed_at = ?, total_pnl = ? "
            "WHERE id = ?",
            (now, total_pnl, position_id),
        )
        self.conn.commit()

    @_synchronized
    def trim_and_log(self, position_id: int, position_updates: dict,
                     trade_kwargs: dict) -> int:
        """Atomic trim: update position + log trade in one transaction.
        Returns trade ID. Rolls back both on failure."""
        try:
            # Update position
            bad = set(position_updates) - self.POSITION_COLUMNS
            if bad:
                raise ValueError(f"Invalid position column(s): {bad}")
            cols = ", ".join(f"{k} = ?" for k in position_updates)
            vals = list(position_updates.values()) + [position_id]
            self.conn.execute(f"UPDATE positions SET {cols} WHERE id = ?", vals)

            # Log trade
            cur = self.conn.execute(
                "INSERT INTO trades "
                "(analyst, message_id, action, asset_type, ticker, direction, "
                "strike, expiry, entry_price, executed_price, quantity, position_size, "
                "trim_fraction, pnl, confidence, raw_message, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_kwargs.get('analyst'), trade_kwargs.get('message_id'),
                 trade_kwargs.get('action'), trade_kwargs.get('asset_type'),
                 trade_kwargs.get('ticker'), trade_kwargs.get('direction'),
                 trade_kwargs.get('strike'), trade_kwargs.get('expiry'),
                 trade_kwargs.get('entry_price'), trade_kwargs.get('executed_price'),
                 trade_kwargs.get('quantity'), trade_kwargs.get('position_size'),
                 trade_kwargs.get('trim_fraction'), trade_kwargs.get('pnl'),
                 trade_kwargs.get('confidence'), trade_kwargs.get('raw_message'),
                 trade_kwargs.get('status', 'executed')),
            )
            self.conn.commit()
            return cur.lastrowid
        except Exception:
            self.conn.rollback()
            raise

    @_synchronized
    def get_open_positions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status = 'open'"
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def get_position_by_ticker(self, ticker: str,
                               analyst: str | None = None) -> dict | None:
        if analyst:
            row = self.conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND analyst = ? "
                "AND status = 'open' ORDER BY opened_at DESC LIMIT 1",
                (ticker, analyst),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND status = 'open' "
                "ORDER BY opened_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def close(self):
        self.conn.close()
