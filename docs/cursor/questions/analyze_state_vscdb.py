#!/usr/bin/env python3
"""
Analyze Cursor workspace state.vscdb content and checkpoint-related traces.

Usage:
  python3 docs/cursor/questions/analyze_state_vscdb.py \
    --db "/path/to/state.vscdb" \
    --output-md docs/cursor/questions/state-vscdb-checkpoint-analysis.md \
    --output-json docs/cursor/questions/state-vscdb-checkpoint-analysis.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


DEFAULT_DB_PATH = (
    "/Users/giraffetree/Library/Application Support/Cursor/User/"
    "workspaceStorage/8c4450604fe9ec9e6a3fae07ff8cf523/state.vscdb"
)
DEFAULT_KEYWORDS = [
    "checkpoint",
    "composer",
    "aichat",
    "agent",
    "snapshot",
    "timeline",
    "rollback",
]


@dataclass
class KeyValueRow:
    table: str
    key: str
    raw_value: Any
    text_value: str
    parsed_json: Optional[Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze Cursor state.vscdb for checkpoint-related signals."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to state.vscdb (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=DEFAULT_KEYWORDS,
        help="Keywords used for scanning keys and JSON leaves.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="How many largest keys to display.",
    )
    parser.add_argument(
        "--output-md",
        default="",
        help="Optional markdown output path.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional JSON output path.",
    )
    return parser.parse_args()


def decode_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def try_parse_json(text: str) -> Optional[Any]:
    text = text.strip()
    if not text:
        return None
    if text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def iter_json_leaves(obj: Any, path: str = "$") -> Iterator[Tuple[str, Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from iter_json_leaves(value, f"{path}.{key}")
        return
    if isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from iter_json_leaves(value, f"{path}[{idx}]")
        return
    yield path, obj


def read_tables(conn: sqlite3.Connection) -> Dict[str, int]:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    counts: Dict[str, int] = {}
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = int(cursor.fetchone()[0])
    return counts


def read_rows(conn: sqlite3.Connection) -> List[KeyValueRow]:
    rows: List[KeyValueRow] = []
    cursor = conn.cursor()
    for table in ("ItemTable", "cursorDiskKV"):
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cursor.fetchone():
            continue

        cursor.execute(f"SELECT key, value FROM {table}")
        for key, raw_value in cursor.fetchall():
            text_value = decode_value(raw_value)
            parsed_json = try_parse_json(text_value)
            rows.append(
                KeyValueRow(
                    table=table,
                    key=key,
                    raw_value=raw_value,
                    text_value=text_value,
                    parsed_json=parsed_json,
                )
            )
    return rows


def key_prefix_counter(rows: Iterable[KeyValueRow]) -> Counter:
    counter: Counter = Counter()
    for row in rows:
        key = row.key
        if "/" in key:
            prefix = key.split("/", 1)[0]
        elif "." in key:
            prefix = key.split(".", 1)[0]
        else:
            prefix = key
        counter[prefix] += 1
    return counter


def search_keywords(rows: Iterable[KeyValueRow], keywords: List[str]) -> Dict[str, Any]:
    normalized_keywords = [k.lower() for k in keywords]
    key_hits: List[Dict[str, Any]] = []
    leaf_hits: List[Dict[str, Any]] = []

    for row in rows:
        key_lower = row.key.lower()
        if any(k in key_lower for k in normalized_keywords):
            key_hits.append(
                {
                    "table": row.table,
                    "key": row.key,
                    "size": len(row.text_value),
                }
            )

        if row.parsed_json is None:
            continue
        for path, value in iter_json_leaves(row.parsed_json):
            value_text = str(value)
            lookup = f"{path} {value_text}".lower()
            matched = [k for k in normalized_keywords if k in lookup]
            if matched:
                leaf_hits.append(
                    {
                        "table": row.table,
                        "key": row.key,
                        "path": path,
                        "matched_keywords": matched,
                        "value_preview": value_text[:240],
                    }
                )

    return {"key_hits": key_hits, "leaf_hits": leaf_hits}


def extract_composer_summary(rows: List[KeyValueRow]) -> Dict[str, Any]:
    composer_row = next((r for r in rows if r.key == "composer.composerData"), None)
    if not composer_row or not isinstance(composer_row.parsed_json, dict):
        return {"found": False}

    data: Dict[str, Any] = composer_row.parsed_json
    all_composers = data.get("allComposers", [])
    if not isinstance(all_composers, list):
        all_composers = []

    # Sort newest first by lastUpdatedAt.
    def sort_key(item: Dict[str, Any]) -> int:
        value = item.get("lastUpdatedAt")
        return int(value) if isinstance(value, (int, float)) else -1

    sorted_composers = sorted(
        [c for c in all_composers if isinstance(c, dict)],
        key=sort_key,
        reverse=True,
    )

    return {
        "found": True,
        "selected_composer_ids": data.get("selectedComposerIds", []),
        "last_focused_composer_ids": data.get("lastFocusedComposerIds", []),
        "composer_count": len(sorted_composers),
        "composers": [
            {
                "composer_id": comp.get("composerId"),
                "type": comp.get("type"),
                "name": comp.get("name"),
                "subtitle": comp.get("subtitle"),
                "created_at": comp.get("createdAt"),
                "last_updated_at": comp.get("lastUpdatedAt"),
                "unified_mode": comp.get("unifiedMode"),
                "force_mode": comp.get("forceMode"),
                "context_usage_percent": comp.get("contextUsagePercent"),
            }
            for comp in sorted_composers
        ],
    }


def extract_panel_composer_mapping(rows: List[KeyValueRow]) -> List[Dict[str, str]]:
    mappings: List[Dict[str, str]] = []
    for row in rows:
        if not row.key.startswith("workbench.panel.composerChatViewPane."):
            continue
        pane_id = row.key.rsplit(".", 1)[-1]
        payload = row.parsed_json
        if not isinstance(payload, dict):
            continue
        for view_key in payload.keys():
            if not view_key.startswith("workbench.panel.aichat.view."):
                continue
            composer_id = view_key.rsplit(".", 1)[-1]
            mappings.append(
                {
                    "pane_id": pane_id,
                    "view_key": view_key,
                    "composer_id": composer_id,
                }
            )
    return mappings


def epoch_ms_to_iso(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(value / 1000).isoformat(timespec="seconds")
    except Exception:
        return ""


def build_report(
    db_path: str,
    table_counts: Dict[str, int],
    rows: List[KeyValueRow],
    keywords: List[str],
    top_n: int,
) -> Dict[str, Any]:
    prefix_counts = key_prefix_counter(rows)
    largest_entries = sorted(
        (
            {"table": row.table, "key": row.key, "size": len(row.text_value)}
            for row in rows
        ),
        key=lambda x: x["size"],
        reverse=True,
    )[:top_n]

    keyword_data = search_keywords(rows, keywords)
    composer_summary = extract_composer_summary(rows)
    panel_mapping = extract_panel_composer_mapping(rows)

    report: Dict[str, Any] = {
        "database_path": db_path,
        "table_counts": table_counts,
        "row_count_total": len(rows),
        "key_prefix_counts": dict(prefix_counts.most_common()),
        "largest_entries": largest_entries,
        "keyword_scan": {
            "keywords": keywords,
            "key_hits_count": len(keyword_data["key_hits"]),
            "leaf_hits_count": len(keyword_data["leaf_hits"]),
            "key_hits": keyword_data["key_hits"],
            "leaf_hits": keyword_data["leaf_hits"],
        },
        "composer_summary": composer_summary,
        "panel_to_composer_mapping": panel_mapping,
    }

    # Add a concise checkpoint-focused section.
    checkpoint_leaf_hits = [
        item
        for item in keyword_data["leaf_hits"]
        if "checkpoint" in item["matched_keywords"]
    ]
    report["checkpoint_focus"] = {
        "checkpoint_key_hits": [
            item
            for item in keyword_data["key_hits"]
            if "checkpoint" in item["key"].lower()
        ],
        "checkpoint_leaf_hits_count": len(checkpoint_leaf_hits),
        "checkpoint_leaf_hits_preview": checkpoint_leaf_hits[:30],
    }

    return report


def render_markdown(report: Dict[str, Any]) -> str:
    table_counts = report["table_counts"]
    largest_entries = report["largest_entries"]
    key_prefix_counts = report["key_prefix_counts"]
    keyword_scan = report["keyword_scan"]
    composer_summary = report["composer_summary"]
    panel_mapping = report["panel_to_composer_mapping"]
    checkpoint_focus = report["checkpoint_focus"]

    lines: List[str] = []
    lines.append("# state.vscdb checkpoint 分析报告")
    lines.append("")
    lines.append(f"- 数据库路径: `{report['database_path']}`")
    lines.append(f"- 总记录数: `{report['row_count_total']}`")
    lines.append("")

    lines.append("## 表与记录数")
    for table, count in table_counts.items():
        lines.append(f"- `{table}`: `{count}`")
    lines.append("")

    lines.append("## Key 前缀分布 (Top 20)")
    for idx, (prefix, count) in enumerate(list(key_prefix_counts.items())[:20], start=1):
        lines.append(f"{idx}. `{prefix}`: `{count}`")
    lines.append("")

    lines.append("## 最大 value 记录 (Top 15)")
    for item in largest_entries:
        lines.append(
            f"- `{item['key']}` (table={item['table']}): {item['size']} chars"
        )
    lines.append("")

    lines.append("## checkpoint 关键词扫描")
    lines.append(f"- 关键词: `{', '.join(keyword_scan['keywords'])}`")
    lines.append(f"- key 命中数: `{keyword_scan['key_hits_count']}`")
    lines.append(f"- JSON 叶子节点命中数: `{keyword_scan['leaf_hits_count']}`")
    lines.append(
        f"- 直接包含 `checkpoint` 的 key 命中数: "
        f"`{len(checkpoint_focus['checkpoint_key_hits'])}`"
    )
    lines.append(
        f"- JSON 叶子节点中包含 `checkpoint` 的命中数: "
        f"`{checkpoint_focus['checkpoint_leaf_hits_count']}`"
    )
    lines.append("")

    if checkpoint_focus["checkpoint_leaf_hits_preview"]:
        lines.append("### checkpoint 叶子命中样例")
        for item in checkpoint_focus["checkpoint_leaf_hits_preview"][:10]:
            lines.append(
                f"- `{item['key']}` -> `{item['path']}`: "
                f"`{item['value_preview']}`"
            )
        lines.append("")

    lines.append("## Composer 会话元数据")
    if not composer_summary.get("found"):
        lines.append("- 未找到 `composer.composerData`")
    else:
        lines.append(f"- composer 总数: `{composer_summary['composer_count']}`")
        lines.append(
            f"- selectedComposerIds 数量: "
            f"`{len(composer_summary['selected_composer_ids'])}`"
        )
        lines.append(
            f"- lastFocusedComposerIds 数量: "
            f"`{len(composer_summary['last_focused_composer_ids'])}`"
        )
        lines.append("")
        lines.append("### 最近活跃 composer (Top 10)")
        for comp in composer_summary["composers"][:10]:
            iso = epoch_ms_to_iso(comp.get("last_updated_at"))
            lines.append(
                f"- `{comp.get('composer_id')}` | "
                f"`{comp.get('name')}` | "
                f"mode={comp.get('unified_mode')} | "
                f"lastUpdatedAt={comp.get('last_updated_at')} ({iso})"
            )
    lines.append("")

    lines.append("## Pane -> Composer 映射 (来自 composerChatViewPane.*)")
    lines.append(f"- 映射数量: `{len(panel_mapping)}`")
    for item in panel_mapping[:20]:
        lines.append(
            f"- pane `{item['pane_id']}` -> composer `{item['composer_id']}`"
        )
    lines.append("")

    lines.append("## 结论 (自动生成)")
    lines.append(
        "- 该 `state.vscdb` 以工作区 UI 状态与会话索引元数据为主，核心在 `ItemTable`。"
    )
    lines.append(
        "- 与 checkpoint 相关的命中主要出现在 prompt/历史文本中，"
        "而非结构化 `checkpoint` key。"
    )
    lines.append(
        "- `composer.composerData` 保存了会话头部元信息（会话名、ID、时间、模式、上下文占用等），"
        "可用于重建会话索引。"
    )
    lines.append(
        "- `workbench.panel.composerChatViewPane.<paneId>` 记录了 pane 到 composer 的关联，"
        "可帮助定位当前 UI 绑定的会话。"
    )
    lines.append(
        "- 如果需要完整 checkpoint 快照（例如精确回滚点内容），"
        "很可能还需联动其他 Cursor 本地存储（本脚本聚焦 `state.vscdb` 单文件）。"
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    # Open read-only to avoid accidental writes.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        table_counts = read_tables(conn)
        rows = read_rows(conn)
    finally:
        conn.close()

    report = build_report(
        db_path=str(db_path),
        table_counts=table_counts,
        rows=rows,
        keywords=args.keywords,
        top_n=args.top_n,
    )
    markdown = render_markdown(report)

    print(markdown)

    if args.output_json:
        output_json_path = Path(args.output_json)
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.output_md:
        output_md_path = Path(args.output_md)
        output_md_path.parent.mkdir(parents=True, exist_ok=True)
        output_md_path.write_text(markdown + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
