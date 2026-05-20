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
  DATAMIND_DB_HOST / DB_HOST   (default: localhost)
  DATAMIND_DB_PORT / DB_PORT   (default: 3306)
  DATAMIND_DB_NAME / DB_NAME
  DATAMIND_DB_USER / DB_USER   (default: root)
  DATAMIND_DB_PASSWORD / DB_PASSWORD
  DB_POOL_SIZE                 (default: 20)
"""

import os
import mysql.connector.pooling
from logger import get_logger

log = get_logger(__name__)

_pool = None


def _build_pool() -> mysql.connector.pooling.MySQLConnectionPool:
    pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
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
    log.info("MySQL connection pool created", size=pool_size)
    return pool


def get_pool() -> mysql.connector.pooling.MySQLConnectionPool:
    """Return the shared pool, creating it on first call (lazy init)."""
    global _pool
    if _pool is None:
        _pool = _build_pool()
    return _pool


def get_internal_conn():
    """
    Borrow a connection from the shared pool.

    IMPORTANT: always call conn.close() when done — this returns the
    connection to the pool, it does NOT close the underlying socket.
    Use a try/finally block to guarantee the return:

        conn = get_internal_conn()
        try:
            ...
        finally:
            conn.close()
    """
    return get_pool().get_connection()
