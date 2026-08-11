"""Backend-agnostic DB access for the UBC Asset Capture platform.

Today the operational store is SQLite (`QR_codes.db`); the Phase C target is
PostgreSQL (`qr_code_db`). This module centralizes the engine differences so the
same application code runs against either backend by setting `DB_BACKEND`.

It is the C4 "shared db layer" pattern-setter (see C4_POSTGRES_CUTOVER_CHECKLIST.md).
Replicate it into the other services (review/, Dashboard/, SDI_process/, API/) as the
cutover proceeds — keep the copies behaviorally identical.

Environment:
  DB_BACKEND        'sqlite' (default) | 'postgres'
  QR_CODES_DB_PATH  SQLite file path  (sqlite backend; defaults to ./data/QR_codes.db)
  QR_PG_DSN         libpq DSN         (postgres backend; defaults to the PoC cluster)

Migration notes:
  * App code keeps writing '?' placeholders; this layer rewrites '?'->'%s' for psycopg2.
  * Identifiers MUST be double-quoted with exact case in SQL — PostgreSQL folds
    unquoted identifiers to lowercase (SQLite did not). e.g. always write "QR_code_ID".
  * `lastrowid` is unavailable on PostgreSQL — use `INSERT ... RETURNING`.
  * Limitation: the '?'->'%s' rewrite is textual; it does not special-case a literal
    '?' inside a quoted string literal (not used in this codebase's queries).
"""
from __future__ import annotations

import os
import sqlite3 as _sqlite3
import threading

# Cross-backend exception tuple so callers can write a single
# `except db.IntegrityError:` regardless of backend. Includes
# psycopg2.IntegrityError (parent of UniqueViolation / ForeignKeyViolation /
# CheckViolation / NotNullViolation) when psycopg2 is installed.
try:
    import psycopg2 as _psycopg2
    IntegrityError = (_sqlite3.IntegrityError, _psycopg2.IntegrityError)
    DatabaseError = (_sqlite3.Error, _psycopg2.Error)
except ImportError:
    IntegrityError = (_sqlite3.IntegrityError,)
    DatabaseError = (_sqlite3.Error,)

_DEFAULT_SQLITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "QR_codes.db")
_DEFAULT_PG_DSN = "host=127.0.0.1 port=5433 dbname=qr_code_db user=developer"

# --- PostgreSQL connection pool ---------------------------------------------
# Opening a PG connection costs ~12ms (measured on the VM). The listing pipeline
# issues hundreds of short queries per request, so connect/close churn dominated
# page-load time. A per-process pool turns each of those into a checkout.
#
# minconn matters more than maxconn here: psycopg2's pool only RETAINS minconn
# idle connections and closes anything returned beyond that, so minconn must
# cover the app's real nesting depth (sld_blueprint holds two at once).
_POOL_MINCONN = int(os.environ.get("DB_POOL_MINCONN", "2"))
_POOL_MAXCONN = int(os.environ.get("DB_POOL_MAXCONN", "5"))

_pool = None
_pool_pid = None
_pool_dsn = None
_pool_lock = threading.Lock()


def pool_enabled() -> bool:
    """Escape hatch: DB_POOL_DISABLED=1 restores connect-per-call behavior."""
    return os.environ.get("DB_POOL_DISABLED", "").strip().lower() not in ("1", "true", "yes")


def _get_pool(dsn: str):
    """Lazy per-process pool. Created on first checkout, i.e. always AFTER the
    gunicorn fork (the app is imported per worker; there is no --preload)."""
    global _pool, _pool_pid, _pool_dsn
    pid = os.getpid()
    with _pool_lock:
        if _pool is not None and (_pool_pid != pid or _pool_dsn != dsn):
            # Inherited across a fork, or the DSN changed. Abandon the reference
            # WITHOUT closing it: those sockets belong to the parent process.
            _pool = None
        if _pool is None:
            from psycopg2.pool import ThreadedConnectionPool
            _pool = ThreadedConnectionPool(_POOL_MINCONN, _POOL_MAXCONN, dsn)
            _pool_pid, _pool_dsn = pid, dsn
        return _pool


def _is_alive(raw) -> bool:
    """Cheap liveness probe so a PG restart can't hand out dead connections."""
    if raw.closed:
        return False
    try:
        cur = raw.cursor()
        try:
            cur.execute("SELECT 1")
        finally:
            cur.close()
        raw.rollback()  # discard the txn the ping opened
        return True
    except Exception:
        return False


def _checkout(dsn: str):
    """Return (pool, raw_connection), discarding and retrying once if stale."""
    pool = _get_pool(dsn)
    last_exc = None
    for _ in range(2):
        raw = pool.getconn()
        if _is_alive(raw):
            return pool, raw
        try:
            pool.putconn(raw, close=True)
        except Exception as exc:  # pragma: no cover - defensive
            last_exc = exc
    import psycopg2
    raise psycopg2.OperationalError(
        f"could not obtain a live pooled connection: {last_exc}"
    )


def _release(pool, raw, close: bool = False) -> None:
    """Return a connection to the pool (or hard-close it when unpooled/broken)."""
    if pool is None:
        try:
            raw.close()
        except Exception:
            pass
        return
    try:
        pool.putconn(raw, close=close or raw.closed)
    except Exception:
        try:
            raw.close()
        except Exception:
            pass


def close_pool() -> None:
    """Close every pooled connection (test teardown / explicit shutdown)."""
    global _pool, _pool_pid, _pool_dsn
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
        _pool = None
        _pool_pid = None
        _pool_dsn = None


def backend() -> str:
    return os.environ.get("DB_BACKEND", "sqlite").strip().lower()


def is_postgres() -> bool:
    return backend() == "postgres"


class _ParamCursor:
    """Wraps a psycopg2 cursor and rewrites '?' placeholders to '%s'."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        if params is not None and params != ():
            sql = sql.replace("%", "%%").replace("?", "%s")
            self._cur.execute(sql, params)
        else:
            self._cur.execute(sql)
        return self

    def executemany(self, sql, seq_params):
        sql = sql.replace("%", "%%").replace("?", "%s")
        self._cur.executemany(sql, seq_params)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size):
        return self._cur.fetchmany(size)

    def __iter__(self):
        return iter(self._cur)

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        raise AttributeError(
            "lastrowid is unavailable on PostgreSQL; use 'INSERT ... RETURNING <pk>'."
        )

    def close(self):
        self._cur.close()


class _PGConnection:
    """Adapter making a psycopg2 connection behave like sqlite3.Connection for this
    codebase: supports conn.execute(sql, params), conn.cursor(), commit/rollback/close,
    and the context-manager commit/rollback protocol.

    Setting conn.row_factory = sqlite3.Row (the codebase's dict-row idiom) is honored:
    subsequent cursors return rows that support both index AND key access (via
    psycopg2.extras.RealDictCursor)."""

    def __init__(self, raw, pool=None):
        # Bypass __setattr__ here so these don't trigger the row_factory branch.
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_cursor_factory", None)
        # When _pool is set, close()/__exit__ RETURN the connection instead of
        # closing it. _released makes that idempotent: callers that both use a
        # `with` block and a `finally: conn.close()` must not putconn twice.
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_released", False)

    def __setattr__(self, name, value):
        if name == "row_factory" and value is not None:
            # Translate sqlite3.Row-style dict access -> RealDictCursor (PG equivalent).
            from psycopg2.extras import RealDictCursor
            object.__setattr__(self, "_cursor_factory", RealDictCursor)
            return
        object.__setattr__(self, name, value)

    def _new_cursor(self):
        cf = self._cursor_factory
        return self._raw.cursor(cursor_factory=cf) if cf else self._raw.cursor()

    def execute(self, sql, params=None):
        return _ParamCursor(self._new_cursor()).execute(sql, params)

    def executemany(self, sql, seq_params):
        cur = _ParamCursor(self._new_cursor())
        cur.executemany(sql, seq_params)
        return cur

    def cursor(self):
        return _ParamCursor(self._new_cursor())

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def _finish(self, close: bool = False):
        """Release the underlying connection exactly once."""
        if self._released:
            return
        object.__setattr__(self, "_released", True)
        if self._pool is not None:
            _release(self._pool, self._raw, close=close)
        else:
            try:
                self._raw.close()
            except Exception:
                pass

    def close(self):
        self._finish()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirror the `with sqlite3.connect() as conn:` idiom (commit/rollback), and ALSO
        # release the handle — each `with` block checks out a connection, so releasing
        # here prevents pool exhaustion (sqlite3's __exit__ commits but does not close).
        broken = False
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        except Exception:
            # A connection we can neither commit nor roll back must not go back
            # into the pool.
            broken = True
        finally:
            self._finish(close=broken)
        return False


def get_connection(sqlite_path: str | None = None, timeout: float | None = None):
    """Return a DB connection for the configured backend.

    SQLite  -> a stdlib sqlite3.Connection (identical to the legacy `_open_db()`).
               `sqlite_path` (if given) is used verbatim, else QR_CODES_DB_PATH / default.
               `timeout` sets the SQLite busy-timeout (lock wait), matching callers that
               pass `sqlite3.connect(path, timeout=N)`.
    Postgres-> a psycopg2 connection wrapped to accept the codebase's '?' SQL style.
               `timeout` (a SQLite busy-wait concept) is not applicable under PG's MVCC and
               is ignored.
    """
    if is_postgres():
        dsn = os.environ.get("QR_PG_DSN", _DEFAULT_PG_DSN)
        if pool_enabled():
            pool, raw = _checkout(dsn)
            return _PGConnection(raw, pool=pool)
        import psycopg2
        return _PGConnection(psycopg2.connect(dsn))

    import sqlite3
    path = sqlite_path or os.environ.get("QR_CODES_DB_PATH", _DEFAULT_SQLITE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return sqlite3.connect(path, timeout=timeout) if timeout is not None else sqlite3.connect(path)


def raw_conn(conn):
    """Return the underlying DBAPI connection for libraries that need a real driver
    handle rather than our wrapper (e.g. pandas.read_sql). For SQLite this is the
    sqlite3.Connection itself; for PostgreSQL it is the wrapped psycopg2 connection."""
    return conn._raw if isinstance(conn, _PGConnection) else conn


# --- cross-engine introspection (replaces PRAGMA / sqlite_master usage) -------
def _pg_introspect_cursor(conn):
    """Return a default-factory (tuple-row) PG cursor for introspection.

    The wrapper honors conn.row_factory=sqlite3.Row by switching to
    psycopg2.extras.RealDictCursor. That breaks tuple-indexing in the helpers
    below (KeyError(0)). For introspection we always want tuple rows, so we
    bypass the wrapper and open a default-factory cursor on the raw connection.
    """
    return raw_conn(conn).cursor()


def list_tables(conn) -> list:
    # Base tables only (parity with SQLite sqlite_master type='table', which excludes views).
    if is_postgres():
        cur = _pg_introspect_cursor(conn)
        try:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'")
            return [r[0] for r in cur.fetchall()]
        finally:
            cur.close()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [r[0] for r in cur.fetchall()]


def has_table(conn, table: str) -> bool:
    if is_postgres():
        cur = _pg_introspect_cursor(conn)
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' AND lower(table_name)=lower(%s)",
                (table,))
            return cur.fetchone() is not None
        finally:
            cur.close()
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)", (table,))
    return cur.fetchone() is not None


def table_columns(conn, table: str) -> list:
    if is_postgres():
        cur = _pg_introspect_cursor(conn)
        try:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                (table,))
            return [r[0] for r in cur.fetchall()]
        finally:
            cur.close()
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return [r[1] for r in cur.fetchall()]
