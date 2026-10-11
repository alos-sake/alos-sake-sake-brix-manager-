import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sake_brix_core import (
    COLUMNS_29,
    calculate_statistics,
    connect_db,
    init_db,
    normalize_import_records,
    restore_records,
    save_measurement,
)


class SakeBrixCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "sake_brix.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_schema_and_save_stats(self):
        init_db(self.db)
        record = {
            "id": "new-test-record-0001",
            "recorded_at": "2026-10-11 12:00:00",
            "brewery": "試験蔵",
            "name": "試験酒",
            "spec_category": "純米酒",
            "brix": 0.0,
            "m1": 0.0,
            "m2": 0.02,
            "m3": None,
            "m4": None,
            "m5": None,
            "m6": None,
            "mean": 0.01,
            "max_val": 0.02,
            "min_val": 0.0,
            "range_val": 0.02,
            "median": 0.01,
            "std_dev": 0.0141421356237,
            "alcohol": None,
            "smv": 0.0,
            "acidity": None,
            "amino_acid": None,
            "rice_polished": None,
            "rice_used": None,
            "yeast": None,
            "memo": None,
        }
        save_measurement(self.db, record)
        conn = connect_db(self.db)
        try:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(measurements)").fetchall()]
            row = conn.execute("SELECT * FROM measurements WHERE id=?", (record["id"],)).fetchone()
            self.assertEqual(len(cols), 29)
            self.assertEqual(set(cols), set(COLUMNS_29))
            self.assertEqual(row["m1"], 0.0)  # Zero is a valid explicitly measured value.
            self.assertIsNone(row["m3"])      # Missing remains NULL.
            self.assertEqual(row["smv"], 0.0)
        finally:
            conn.close()

    def test_statistics_one_and_multiple(self):
        one = calculate_statistics([4.25, None, None])
        self.assertEqual(one["mean"], 4.25)
        self.assertIsNone(one["std_dev"])
        multi = calculate_statistics([4.2, 4.4, None, 4.3])
        self.assertAlmostEqual(multi["mean"], 4.3)
        self.assertEqual(multi["max_val"], 4.4)
        self.assertEqual(multi["min_val"], 4.2)
        self.assertAlmostEqual(multi["range_val"], 0.2)
        self.assertAlmostEqual(multi["median"], 4.3)
        self.assertIsNotNone(multi["std_dev"])

    def test_migrates_integer_id_and_legacy_columns_without_fabricating_stats(self):
        conn = sqlite3.connect(self.db)
        conn.execute("""
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                measured_at TEXT,
                brewery_name TEXT,
                brand_name TEXT,
                brix REAL,
                sake_meter REAL,
                amino REAL,
                polish REAL,
                rice TEXT,
                alcohol REAL,
                acidity REAL,
                yeast TEXT,
                memo TEXT,
                count INTEGER,
                unit TEXT,
                device_info TEXT
            )
        """)
        conn.execute(
            "INSERT INTO measurements (measured_at, brewery_name, brand_name, brix, sake_meter, amino, polish, rice, alcohol, acidity, yeast, memo, count, unit, device_info) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2024-01-02 10:00:00", "旧蔵", "旧銘柄", 14.6, 0.0, 0.0, 0.0, "山田錦", 15.0, 1.4, "協会酵母", "旧メモ", 3, "°Bx", "旧機器"),
        )
        conn.commit()
        conn.close()

        status = init_db(self.db)
        self.assertTrue(status["migrated"])
        self.assertEqual(status["migrated_count"], 1)
        self.assertTrue(Path(status["backup_path"]).exists())
        conn = connect_db(self.db)
        try:
            row = conn.execute("SELECT * FROM measurements").fetchone()
            self.assertEqual(row["brewery"], "旧蔵")
            self.assertEqual(row["name"], "旧銘柄")
            self.assertEqual(row["brix"], 14.6)
            self.assertEqual(row["mean"], 14.6)
            self.assertEqual(row["smv"], 0.0)
            self.assertEqual(row["amino_acid"], 0.0)
            self.assertEqual(row["rice_polished"], 0.0)
            self.assertIsNone(row["max_val"])
            self.assertIsNone(row["min_val"])
            self.assertIn("count: 3", row["memo"])
            self.assertIn("unit: °Bx", row["memo"])
            self.assertIn("device_info: 旧機器", row["memo"])
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='measurements_old'").fetchone())
        finally:
            conn.close()

    def test_migration_failure_rolls_back_source_table(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY, brewery_name TEXT, brand_name TEXT, brix TEXT)")
        conn.execute("INSERT INTO measurements (id, brewery_name, brand_name, brix) VALUES (1, '蔵', '酒', 'not-a-number')")
        conn.commit()
        conn.close()

        with self.assertRaises(RuntimeError):
            init_db(self.db)
        conn = sqlite3.connect(self.db)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("measurements", tables)
            self.assertNotIn("measurements_old", tables)
            row = conn.execute("SELECT brewery_name, brand_name, brix FROM measurements").fetchone()
            self.assertEqual(row, ("蔵", "酒", "not-a-number"))
        finally:
            conn.close()
        self.assertTrue(list(self.root.glob("sake_brix_backup_mig_*.db")))

    def test_restore_same_legacy_backup_is_idempotent(self):
        init_db(self.db)
        data = [{
            "record_id": 7,
            "measured_at": "2025-02-03 12:30:00",
            "brewery_name": "復元蔵",
            "brand_name": "復元酒",
            "brix": 12.3,
            "sake_meter": 0,
            "memo": "復元メモ",
        }]
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        normalized = normalize_import_records(data, source_bytes=raw)
        first = restore_records(self.db, normalized)
        second = restore_records(self.db, normalize_import_records(data, source_bytes=raw))
        self.assertEqual(first, {"added": 1, "skipped": 0})
        self.assertEqual(second, {"added": 0, "skipped": 1})
        conn = connect_db(self.db)
        try:
            row = conn.execute("SELECT * FROM measurements").fetchone()
            self.assertEqual(row["name"], "復元酒")
            self.assertEqual(row["smv"], 0.0)
            self.assertEqual(row["mean"], 12.3)
            self.assertIsNone(row["max_val"])
        finally:
            conn.close()

    def test_restore_rejects_missing_required_name(self):
        with self.assertRaises(ValueError):
            normalize_import_records([{"id": "bad-1", "brewery": "蔵"}])

    def test_restore_skips_nan_and_preserves_zero_alias(self):
        normalized = normalize_import_records([{
            "id": "nan-test-id",
            "brewery": "蔵",
            "name": "酒",
            "smv": float("nan"),
            "sake_meter": 0.0,
        }])
        self.assertEqual(normalized[0]["smv"], 0.0)

    def test_existing_migration_table_is_never_dropped_automatically(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY, brewery_name TEXT, brand_name TEXT)")
        conn.execute("INSERT INTO measurements VALUES (1, '新しい旧蔵', '新しい旧酒')")
        conn.execute("CREATE TABLE measurements_old (id TEXT, keep_me TEXT)")
        conn.execute("INSERT INTO measurements_old VALUES ('old', 'important recovery data')")
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError):
            init_db(self.db)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT keep_me FROM measurements_old").fetchone()[0], "important recovery data")
            self.assertEqual(conn.execute("SELECT brand_name FROM measurements").fetchone()[0], "新しい旧酒")
        finally:
            conn.close()

    def test_extra_legacy_columns_are_migrated_into_memo(self):
        # Start with the current schema but add an older application-specific field.
        from sake_brix_core import CREATE_TABLE_SQL
        conn = sqlite3.connect(self.db)
        conn.execute(CREATE_TABLE_SQL)
        conn.execute("ALTER TABLE measurements ADD COLUMN device_info TEXT")
        conn.execute(
            "INSERT INTO measurements (id, recorded_at, brewery, name, brix, mean, device_info) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("keep-extra-legacy-field", "2025-01-01 00:00:00", "蔵", "酒", 13.2, 13.2, "測定器X"),
        )
        conn.commit()
        conn.close()
        status = init_db(self.db)
        self.assertTrue(status["migrated"])
        conn = connect_db(self.db)
        try:
            row = conn.execute("SELECT * FROM measurements").fetchone()
            self.assertIn("device_info: 測定器X", row["memo"])
        finally:
            conn.close()

    def test_normal_start_does_not_create_repeated_migration_backups(self):
        init_db(self.db)
        before = list(self.root.glob("sake_brix_backup_mig_*.db"))
        again = init_db(self.db)
        after = list(self.root.glob("sake_brix_backup_mig_*.db"))
        self.assertFalse(again["migrated"])
        self.assertEqual(len(before), len(after))

    def test_empty_restore_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_import_records([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
