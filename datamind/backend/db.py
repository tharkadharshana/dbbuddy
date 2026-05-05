import mysql.connector
import os
from typing import List, Optional, Dict, Any


def get_connection():
    """Return a MySQL connection using env variables."""
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", ""),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def get_table_schemas(conn, tables: Optional[List[str]]) -> Dict[str, Any]:
    """
    Return column info for each table.
    If tables is None, fetch all tables in the database.
    """
    cursor = conn.cursor()

    if tables is None:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]

    schemas = {}
    for table in tables:
        cursor.execute(f"DESCRIBE `{table}`")
        columns = cursor.fetchall()
        schemas[table] = [
            {
                "name": col[0],
                "type": col[1],
                "null": col[2],
                "key": col[3],
                "default": col[4],
            }
            for col in columns
        ]

    return schemas


def schema_to_text(schemas: Dict[str, Any]) -> str:
    """Convert schema dict to a readable string for LLM prompts."""
    lines = []
    for table, columns in schemas.items():
        col_defs = ", ".join(f"`{c['name']}` {c['type']}" for c in columns)
        lines.append(f"Table `{table}`: ({col_defs})")
    return "\n".join(lines)
