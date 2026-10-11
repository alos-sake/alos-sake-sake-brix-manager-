"""Core database and data-normalization functions for the sake Brix app.

This module intentionally has no Streamlit or Google SDK dependency so its
migration and restore logic can be tested independently.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import numbers
import sqlite3
import statistics
import uuid
from pathlib import Path
from typing import Any, Iterable

COLUMNS_29 = [
    "id", "recorded_at", "brewery", "name", "by_year", "lot_date",
    "spec_category", "processing_state", "brix", "m1", "m2", "m3",
    "m4", "m5", "m6", "mean", "max_val", "min_val", "range_val",
    "median", "std_dev", "alcohol", "smv", "acidity", "amino_acid",
    "rice_polished", "rice_used", "yeast", "memo",
]

NUMERIC_COLUMNS = {
    "brix", "m1", "m2", "m3", "m4", "m5", "m6", "mean", "max_val",
    "min_val", "range_val", "median", "std_dev", "alcohol", "smv",
    "acidity", "amino_acid", "rice_polished",
}

CREATE_TABLE_SQL = """
CREATE TABLE measurements (
    id TEXT PRIMARY KEY,
    recorded_at TEXT,
    brewery TEXT,
    name TEXT,
    by_year TEXT,
    lot_date TEXT,
    spec_category TEXT,
    processing_state TEXT,
    brix REAL,
    m1 REAL,
    m2 REAL,
    m3 REAL,
    m4 REAL,
    m5 REAL,
    m6 REAL,
    mean REAL,
    max_val REAL,
    min_val REAL,
    range_val REAL,
    median REAL,
    std_dev REAL,
    alcohol REAL,
    smv REAL,
    acidity REAL,
    amino_acid REAL,
    rice_polished REAL,
    rice_used TEXT,
    yeast TEXT,
    memo TEXT
)
"""

ALIASES = {
    "id": ("id", "record_id"),
    "recorded_at": ("recorded_at", "measured_at", "measurement_date", "created_at"),
    "brewery": ("brewery", "brewery_name"),
    "name": ("name", "brand_name", "product_name", "sake_name"),
    "by_year": ("by_year", "brew_year", "brewing_year"),
    "lot_date": ("lot_date", "manufacture_date", "manufacturing_date", "lot"),
    "spec_category": ("spec_category", "category", "classification"),
    "processing_state": ("processing_state", "processing", "state"),
    "brix": ("brix", "sugar_content", "sugar"),
    "m1": ("m1",), "m2": ("m2",), "m3": ("m3",),
    "m4": ("m4",), "m5": ("m5",), "m6": ("m6",),
    "mean": ("mean", "average", "avg"),
    "max_val": ("max_val", "maximum", "max"),
    "min_val": ("min_val", "minimum", "min"),
    "range_val": ("range_val", "range"),
    "median": ("median",),
    "std_dev": ("std_dev", "stdev", "standard_deviation"),
    "alcohol": ("alcohol", "alcohol_content", "abv"),
    "smv": ("smv", "sake_meter", "nihonshu_do"),
    "acidity": ("acidity", "acid"),
    "amino_acid": ("amino_acid", "amino", "amino_acid_level"),
    "rice_polished": ("rice_polished", "polish", "polishing_rate", "seimai_buai"),
    "rice_used": ("rice_used", "rice", "rice_variety"),
    "yeast": ("yeast", "yeast_used"),
    "memo": ("memo", "notes", "comment", "tasting_note"),
}

# These source keys have a destination in the formal schema and need not be
# copied a second time into memo. Any other non-empty source fields are kept in
# memo so a legacy field is not silently discarded.
KNOWN_SOURCE_KEYS = {key for aliases in ALIASES.values() for key in aliases}
KNOWN_SOURCE_KEYS.update({"count", "unit", "device_info"})


def get_jst_now() -> str:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() in {"nan", "none", "null"}
    if isinstance(value, numbers.Real):
        try:
            if math.isnan(float(value)):
                return True
        except (TypeError, ValueError):
            pass
    # Handles scalar pandas/numpy NA values without requiring pandas here.
    try:
        result = value != value
        if bool(result):
            return True
    except Exception:
        pass
    return False


def _first_value(record: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in record and not _is_missing(record[name]):
            return record[name]
    return None


def _as_text(value: Any, default: str | None = None) -> str | None:
    if _is_missing(value):
        return default
    return str(value).strip()


def _as_float(value: Any, field_name: str, *, allow_percent: bool = False) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        raise ValueError(f"'{field_name}' は数値である必要があります。")
    if isinstance(value, str):
        raw = value.strip().replace(",", "")
        if allow_percent and raw.endswith("%"):
            raw = raw[:-1].strip()
        value = raw
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{field_name}' の値 {value!r} を数値に変換できません。") from exc
    if not math.isfinite(result):
        raise ValueError(f"'{field_name}' に有限の数値を指定してください。")
    return result


def _json_safe_id(raw_id: Any, fallback_id: str | None) -> str:
    if _is_missing(raw_id) or isinstance(raw_id, (int, float)) and not isinstance(raw_id, bool):
        return fallback_id or str(uuid.uuid4())
    text = str(raw_id).strip()
    return text if text else (fallback_id or str(uuid.uuid4()))


def _append_legacy_fields(record: dict[str, Any], memo: str | None) -> str | None:
    extra: list[str] = []
    for key, value in record.items():
        if (key in KNOWN_SOURCE_KEYS and key not in {"count", "unit", "device_info"}) or _is_missing(value):
            continue
        # Avoid serializing nested arbitrary structures as Python repr.
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(value)
        extra.append(f"{key}: {rendered}")
    if not extra:
        return memo
    suffix = "[旧データ項目: " + "; ".join(extra) + "]"
    return f"{memo}\n{suffix}" if memo else suffix


def normalize_import_record(raw: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any]:
    """Convert a current or legacy JSON record to exactly the current schema.

    Missing brix/mean values are cross-filled when one of those representative
    values exists. Other statistics are *not* fabricated from a single Brix.
    """
    if not isinstance(raw, dict):
        raise ValueError("各レコードはJSONオブジェクトである必要があります。")

    out: dict[str, Any] = {}
    out["id"] = _json_safe_id(_first_value(raw, ALIASES["id"]), fallback_id)

    brewery = _as_text(_first_value(raw, ALIASES["brewery"]))
    name = _as_text(_first_value(raw, ALIASES["name"]))
    if not brewery or not name:
        raise ValueError(f"ID {out['id']} のレコードに蔵元名または銘柄名がありません。")
    out["brewery"] = brewery
    out["name"] = name

    out["recorded_at"] = _as_text(_first_value(raw, ALIASES["recorded_at"]), get_jst_now())
    out["by_year"] = _as_text(_first_value(raw, ALIASES["by_year"]))
    out["lot_date"] = _as_text(_first_value(raw, ALIASES["lot_date"]))
    out["spec_category"] = _as_text(_first_value(raw, ALIASES["spec_category"]), "未選択")
    out["processing_state"] = _as_text(_first_value(raw, ALIASES["processing_state"]), "")
    out["rice_used"] = _as_text(_first_value(raw, ALIASES["rice_used"]))
    out["yeast"] = _as_text(_first_value(raw, ALIASES["yeast"]))
    memo = _as_text(_first_value(raw, ALIASES["memo"]))
    out["memo"] = _append_legacy_fields(raw, memo)

    for column in NUMERIC_COLUMNS:
        alias_names = ALIASES.get(column, (column,))
        raw_value = _first_value(raw, alias_names)
        out[column] = _as_float(raw_value, column, allow_percent=(column == "rice_polished"))

    if out["brix"] is None and out["mean"] is not None:
        out["brix"] = out["mean"]
    if out["mean"] is None and out["brix"] is not None:
        out["mean"] = out["brix"]
    return out


def record_tuple(record: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(record.get(column) for column in COLUMNS_29)


def calculate_statistics(measurements: Iterable[float | None]) -> dict[str, float | None]:
    valid = [float(value) for value in measurements if value is not None]
    if not valid:
        raise ValueError("糖度の有効な実測値を少なくとも1つ指定してください。")
    maximum = max(valid)
    minimum = min(valid)
    mean_value = statistics.mean(valid)
    return {
        "brix": mean_value,
        "mean": mean_value,
        "max_val": maximum,
        "min_val": minimum,
        "range_val": maximum - minimum,
        "median": statistics.median(valid),
        "std_dev": statistics.stdev(valid) if len(valid) > 1 else None,
    }


def save_measurement(db_path: str | Path, record: dict[str, Any]) -> str:
    """Insert one already-validated record atomically; return its ID."""
    normalized = normalize_import_record(record)
    sql = f"INSERT INTO measurements ({', '.join(COLUMNS_29)}) VALUES ({', '.join(['?'] * len(COLUMNS_29))})"
    conn = connect_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(sql, record_tuple(normalized))
        conn.commit()
        return normalized["id"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _legacy_row_to_record(old: dict[str, Any], used_ids: set[str]) -> dict[str, Any]:
    raw_id = _first_value(old, ("id", "record_id"))
    # Existing integer primary keys are changed to UUIDs. Text IDs are kept if
    # non-empty and unique; collisions are resolved with a fresh UUID.
    if isinstance(raw_id, int) and not isinstance(raw_id, bool):
        new_id = str(uuid.uuid4())
    else:
        new_id = _as_text(raw_id)
        if not new_id:
            new_id = str(uuid.uuid4())
    if new_id in used_ids:
        new_id = str(uuid.uuid4())
    used_ids.add(new_id)

    # Build canonical values from aliases while distinguishing 0 from missing.
    canonical = dict(old)
    canonical["id"] = new_id
    canonical["recorded_at"] = _first_value(old, ALIASES["recorded_at"]) or get_jst_now()
    canonical["brewery"] = _first_value(old, ALIASES["brewery"]) or "不明蔵元"
    canonical["name"] = _first_value(old, ALIASES["name"]) or "不明銘柄"
    canonical["by_year"] = _first_value(old, ALIASES["by_year"])
    canonical["lot_date"] = _first_value(old, ALIASES["lot_date"])
    canonical["spec_category"] = _first_value(old, ALIASES["spec_category"]) or "未選択"
    canonical["processing_state"] = _first_value(old, ALIASES["processing_state"]) or ""
    canonical["memo"] = _first_value(old, ALIASES["memo"])
    for key, aliases in ALIASES.items():
        if key in {"id", "recorded_at", "brewery", "name", "by_year", "lot_date", "spec_category", "processing_state", "memo"}:
            continue
        canonical[key] = _first_value(old, aliases)

    # Never invent max/min/median/range from a single representative Brix.
    canonical["brix"] = _as_float(canonical.get("brix"), "brix", allow_percent=False)
    canonical["mean"] = _as_float(canonical.get("mean"), "mean", allow_percent=False)
    if canonical["brix"] is None:
        canonical["brix"] = canonical["mean"]
    if canonical["mean"] is None:
        canonical["mean"] = canonical["brix"]
    for col in ("m1", "m2", "m3", "m4", "m5", "m6", "max_val", "min_val", "range_val", "median", "std_dev", "alcohol", "smv", "acidity", "amino_acid", "rice_polished"):
        canonical[col] = _as_float(canonical.get(col), col, allow_percent=(col == "rice_polished"))

    canonical["rice_used"] = _as_text(canonical.get("rice_used"))
    canonical["yeast"] = _as_text(canonical.get("yeast"))

    extra_keys = [
        key for key, value in old.items()
        if (key not in KNOWN_SOURCE_KEYS or key in {"count", "unit", "device_info"})
        and not _is_missing(value)
    ]
    if extra_keys:
        extra_info = "; ".join(f"{key}: {old[key]}" for key in extra_keys)
        canonical["memo"] = f"{canonical['memo']}\n[旧DB項目: {extra_info}]" if canonical.get("memo") else f"[旧DB項目: {extra_info}]"
    for column in COLUMNS_29:
        canonical.setdefault(column, None)
    return {column: canonical.get(column) for column in COLUMNS_29}


def _backup_database(conn: sqlite3.Connection, backup_path: Path) -> None:
    backup_conn: sqlite3.Connection | None = None
    try:
        backup_conn = sqlite3.connect(str(backup_path), timeout=15)
        conn.backup(backup_conn)
        check = backup_conn.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"バックアップDBの整合性検査に失敗しました: {check[0] if check else '結果なし'}")
        backup_conn.close()
        backup_conn = None
        if not backup_path.exists() or backup_path.stat().st_size <= 0:
            raise RuntimeError("バックアップファイルが作成されていないか、空です。")
    except Exception:
        if backup_conn is not None:
            backup_conn.close()
        try:
            if backup_path.exists():
                backup_path.unlink()
        except OSError:
            pass
        raise


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _schema_needs_migration(conn: sqlite3.Connection) -> tuple[bool, list[str], dict[str, Any]]:
    info = conn.execute("PRAGMA table_info(measurements)").fetchall()
    columns = [row["name"] for row in info]
    by_name = {row["name"]: dict(row) for row in info}
    id_info = by_name.get("id", {})
    needs = (
        set(columns) != set(COLUMNS_29)
        or str(id_info.get("type", "")).strip().upper() != "TEXT"
        or int(id_info.get("pk", 0) or 0) != 1
        or any(alias in columns for alias in ("brand_name", "brewery_name"))
    )
    return needs, columns, by_name


def init_db(db_path: str | Path) -> dict[str, Any]:
    """Create the DB or safely migrate a legacy measurements table.

    Returns a small status dictionary. A failed backup or migration raises an
    exception and does not silently continue with a partially upgraded DB.
    """
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_db(db_path)
    result: dict[str, Any] = {"migrated": False, "backup_path": None, "migrated_count": 0}
    try:
        has_measurements = _table_exists(conn, "measurements")
        has_old = _table_exists(conn, "measurements_old")

        if not has_measurements:
            if has_old:
                raise RuntimeError(
                    "measurements テーブルがなく measurements_old が残っています。"
                    "既存データを自動削除せず起動を停止しました。DBファイルを複製し、退避テーブルを確認してください。"
                )
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()
            _create_indexes(conn)
            conn.commit()
            return result

        needs_migration, _, _ = _schema_needs_migration(conn)
        if not needs_migration:
            if has_old:
                raise RuntimeError(
                    "正式な measurements と measurements_old が両方存在します。"
                    "退避テーブルの内容を確認するまで自動削除・移行は行いません。"
                )
            _create_indexes(conn)
            conn.commit()
            return result

        if has_old:
            raise RuntimeError(
                "スキーマ移行が必要ですが measurements_old が既に存在します。"
                "安全のため移行を中止しました。既存退避テーブルを確認してください。"
            )

        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = db_path.parent / f"{db_path.stem}_backup_mig_{stamp}_{uuid.uuid4().hex[:6]}.db"
        # Backup must succeed and pass integrity_check before any destructive DDL.
        _backup_database(conn, backup_path)
        result["backup_path"] = str(backup_path)

        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ALTER TABLE measurements RENAME TO measurements_old")
            conn.execute(CREATE_TABLE_SQL)
            old_cursor = conn.execute("SELECT * FROM measurements_old")
            old_columns = [column[0] for column in old_cursor.description]
            old_rows = [dict(zip(old_columns, row)) for row in old_cursor.fetchall()]
            used_ids: set[str] = set()
            insert_sql = f"INSERT INTO measurements ({', '.join(COLUMNS_29)}) VALUES ({', '.join(['?'] * len(COLUMNS_29))})"
            migrated_count = 0
            for old_row in old_rows:
                new_record = _legacy_row_to_record(old_row, used_ids)
                conn.execute(insert_sql, record_tuple(new_record))
                migrated_count += 1

            new_count = int(conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0])
            if new_count != len(old_rows) or migrated_count != len(old_rows):
                raise RuntimeError(
                    f"移行件数が一致しません (旧: {len(old_rows)}件 / 変換: {migrated_count}件 / 新: {new_count}件)"
                )
            # Drop the old table only after all rows have been copied and checked.
            conn.execute("DROP TABLE measurements_old")
            conn.commit()
            result["migrated"] = True
            result["migrated_count"] = migrated_count
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(
                f"DB移行に失敗したためトランザクションをロールバックしました。"
                f"元DBの状態を確認してください。退避バックアップ: {backup_path}。詳細: {exc}"
            ) from exc

        _create_indexes(conn)
        conn.commit()
        return result
    finally:
        conn.close()


def _create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_recorded_at ON measurements(recorded_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_brewery ON measurements(brewery)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_name ON measurements(name)")


def normalize_import_records(records: list[dict[str, Any]], source_bytes: bytes | None = None) -> list[dict[str, Any]]:
    """Validate an entire JSON backup before opening a restore transaction.

    Missing/numeric legacy IDs get deterministic UUIDs tied to this exact backup
    and row position, so re-uploading the same file does not create duplicates.
    """
    if not isinstance(records, list):
        raise ValueError("JSONの最上位はレコード配列である必要があります。")
    if not records:
        raise ValueError("バックアップにレコードがありません。復元するデータは0件です。")
    seed = hashlib.sha256(source_bytes if source_bytes is not None else json.dumps(records, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        deterministic_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sake-brix-import:{seed}:{index}"))
        normalized.append(normalize_import_record(raw, fallback_id=deterministic_id))
    return normalized


def restore_records(db_path: str | Path, records: list[dict[str, Any]]) -> dict[str, int]:
    """Atomically restore validated records and skip existing/duplicate IDs."""
    sql = f"INSERT INTO measurements ({', '.join(COLUMNS_29)}) VALUES ({', '.join(['?'] * len(COLUMNS_29))})"
    conn = connect_db(db_path)
    added = skipped = 0
    seen_in_file: set[str] = set()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for record in records:
            rec_id = record["id"]
            if rec_id in seen_in_file:
                skipped += 1
                continue
            seen_in_file.add(rec_id)
            exists = conn.execute("SELECT 1 FROM measurements WHERE id = ?", (rec_id,)).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(sql, record_tuple(record))
            added += 1
        conn.commit()
        return {"added": added, "skipped": skipped}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def export_records(db_path: str | Path) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(f"SELECT {', '.join(COLUMNS_29)} FROM measurements ORDER BY recorded_at DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
