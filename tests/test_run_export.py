import argparse
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.schema import NormalizedItem
from connectors.storage import init_sqlite, upsert_item

import run


def _make_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    init_sqlite(db)
    upsert_item(db, NormalizedItem(
        connector="fixture",
        source_id="public-1",
        title="Public note",
        summary="Public summary",
        fulltext="Public fulltext body",
        created_at="2024-02-11",
    ))
    upsert_item(db, NormalizedItem(
        connector="fixture",
        source_id="restricted-1",
        title="Restricted note",
        summary="Restricted summary",
        fulltext="Restricted fulltext body",
        created_at="2024-02-12",
        rights={"allow_abstract": True, "allow_fulltext": False, "can_export": False, "export_policy": "none"},
    ))
    upsert_item(db, NormalizedItem(
        connector="fixture",
        source_id="abstract-only-1",
        title="Abstract-only note",
        summary="Abstract-only summary",
        fulltext="This fulltext must NOT appear in the export",
        created_at="2024-02-13",
        rights={"allow_abstract": True, "allow_fulltext": False, "can_export": True, "export_policy": "abstract_only"},
    ))
    return db


class RunExportTests(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _export_json(self, **kwargs):
        defaults = {
            "db": str(self.db),
            "format": "json",
            "output": None,
            "connector": None,
            "limit": None,
        }
        defaults.update(kwargs)
        args = argparse.Namespace(**defaults)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            run.cmd_export(args)
        finally:
            sys.stdout = old
        return json.loads(buf.getvalue())

    def test_excludes_items_without_export_rights(self):
        records = self._export_json()
        source_ids = {r["source_id"] for r in records}
        self.assertIn("public-1", source_ids)
        self.assertIn("abstract-only-1", source_ids)
        self.assertNotIn("restricted-1", source_ids)

    def test_abstract_only_policy_strips_fulltext(self):
        records = self._export_json()
        by_id = {r["source_id"]: r for r in records}
        abstract_rec = by_id["abstract-only-1"]
        self.assertEqual(abstract_rec["summary"], "Abstract-only summary")
        self.assertIsNone(abstract_rec["fulltext"])

    def test_full_policy_includes_fulltext(self):
        records = self._export_json()
        by_id = {r["source_id"]: r for r in records}
        self.assertEqual(by_id["public-1"]["fulltext"], "Public fulltext body")

    def test_connector_filter(self):
        records = self._export_json(connector="nonexistent")
        self.assertEqual(records, [])

    def test_limit(self):
        records = self._export_json(limit=1)
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
