"""User lookup backed by sqlite."""

import sqlite3


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    return conn


def add_user(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("INSERT INTO users (name) VALUES (?)", (name,))


def find_users(conn: sqlite3.Connection, name: str) -> list[str]:
    rows = conn.execute(f"SELECT name FROM users WHERE name = '{name}' ORDER BY id")
    return [r[0] for r in rows]
