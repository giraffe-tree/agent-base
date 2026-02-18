#!/usr/bin/env python3
"""
Print all contents from Cursor state.vscdb.

Usage:
  python3 docs/cursor/questions/print_state_vscdb_all.py \
    --db "/path/to/state.vscdb"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Tuple


DEFAULT_DB_PATH = (
    "/Users/giraffetree/Library/Application Support/Cursor/User/"
    "workspaceStorage/d4e14ce50000f641d829efb839eebd9c/state.vscdb"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print every key/value row in state.vscdb."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to state.vscdb (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--pretty-json",
        action="store_true",
        help="Pretty-print JSON values (slower but easier to read).",
    )
    return parser.parse_args()


def decode_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def try_parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def iter_tables(conn: sqlite3.Connection) -> Iterable[str]:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    for (name,) in cursor.fetchall():
        yield name


def iter_rows(conn: sqlite3.Connection, table: str) -> Iterable[Tuple[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(f"SELECT key, value FROM {table} ORDER BY key")
    yield from cursor.fetchall()


def print_table(conn: sqlite3.Connection, table: str, pretty_json: bool) -> None:
    rows = list(iter_rows(conn, table))
    print(f"\n=== TABLE: {table} | rows={len(rows)} ===")
    for idx, (key, raw_value) in enumerate(rows, start=1):
        text = decode_value(raw_value)
        parsed = try_parse_json(text)
        print(f"\n--- [{idx}/{len(rows)}] key: {key}")
        if parsed is not None and pretty_json:
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        else:
            print(text)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = list(iter_tables(conn))
        if not tables:
            print("No tables found.")
            return
        print(f"Database: {db_path}")
        print(f"Tables: {', '.join(tables)}")
        for table in tables:
            print_table(conn, table, pretty_json=args.pretty_json)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
