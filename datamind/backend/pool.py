"""
pool.py
=======
Shared MySQL connection pool for DataMind's internal DB.

All internal DB access (auth, integrations, billing, embed) should use
get_internal_conn() from this module instead of opening raw connections.

mysql.connector.pooling.MySQLConnectionPool manages a fixed number of
persistent TCP connections. Callers borrow a connection and return it
via conn.close() — this does NOT close the socket, it returns the
connection to the pool for the next caller.

Config via env vars:
  DATAMIND_DB_HOST / DB_HOST        (default: localhost)
  DATAMIND_DB_PORT / DB_PORT        (default: 3306)
  DATAMIND_DB_NAME / DB_NAME
  DATAMIND_DB_USER / DB_USER        (default: root)
  DATAMIND_DB_PASSWORD / DB_PASSWORD
  DB_POOL_SIZE                      (default: 20)
  DB_POOL_BORROW_TIMEOUT            (default: 5s — raises immediately instead
                                     of blocking forever when pool is exhausted)
"""

import os
import threading
import mysql.connector.pooling
from logger import get_logger

log = get_logger(__name__)

_pool = None
_pool_semaphore: threading.Semaphore = None
_DB_POOL_BORROW_TIMEOUT = int(os.getenv("DB_POOL_BORROW_TIMEOUT", "5"))


class _PooledConnWrapper:
    """
    Thin proxy that releases the semaphore permit when the connection is
    returned to the pool via .close(). All other attribute accesses are
    forwarded to the underlying pooled connection.
    """
    __slots__ = ("_conn", "_sem", "_released")

    def __init__(self, conn, sem: threading.Semaphore):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_sem", sem)
        object.__setattr__(self, "_released", False)

    def close(self):
        conn = object.__getattribute__(self, "_conn")
        sem = object.__getattribute__(self, "_sem")
        released = object.__getattribute__(self, "_released")
        conn.close()
        if not released:
            object.__setattr__(self, "_released", True)
            sem.release()

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _build_pool() -> mysql.connector.pooling.MySQLConnectionPool:
    global _pool_semaphore
    pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
    _pool_semaphore = threading.Semaphore(pool_size)
    pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name          = "datamind_internal",
        pool_size          = pool_size,
        pool_reset_session = True,
        host     = os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database = os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user     = os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout = 10,
    )
    log.info("MySQL connection pool created", size=pool_size,
             borrow_timeout=_DB_POOL_BORROW_TIMEOUT)
    return pool


def get_pool() -> mysql.connector.pooling.MySQLConnectionPool:
    """Return the shared pool, creating it on first call (lazy init)."""
    global _pool
    if _pool is None:
        _pool = _build_pool()
    return _pool


def get_internal_conn() -> _PooledConnWrapper:
    """
    Borrow a connection from the shared pool.

    Raises RuntimeError after DB_POOL_BORROW_TIMEOUT seconds if the pool is
    exhausted — prevents threads from blocking indefinitely and starving the
    event loop. Default timeout: 5 seconds.

    IMPORTANT: always call conn.close() when done — this returns the
    connection to the pool, it does NOT close the underlying socket.
    Use a try/finally block to guarantee the return:

        conn = get_internal_conn()
        try:
            ...
        finally:
            conn.close()
    """
    sem = _pool_semaphore or (get_pool() and _pool_semaphore)
    timeout = _DB_POOL_BORROW_TIMEOUT
    acquired = sem.acquire(timeout=timeout)
    if not acquired:
        log.error("DB pool exhausted", timeout=timeout, pool_size=int(os.getenv("DB_POOL_SIZE", "20")))
        raise RuntimeError(
            f"Database pool exhausted — could not acquire a connection within {timeout}s. "
            "Try again in a moment or contact support if this persists."
        )
    try:
        conn = get_pool().get_connection()
    except Exception:
        sem.release()
        raise
    return _PooledConnWrapper(conn, sem)
