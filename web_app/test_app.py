import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
import types
from unittest.mock import patch

# Route tests mock database access, so they only need the import surface.
if "psycopg2" not in sys.modules:
    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.Error = type("DatabaseError", (Exception,), {})
    psycopg2.extras = types.ModuleType("psycopg2.extras")
    psycopg2.extras.DictCursor = object
    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.extras"] = psycopg2.extras

import app as application


class AppTestCase(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = application.app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username="admin", role="admin")

    def test_authenticated_data_page_and_export(self):
        self.assertEqual(self.client.get("/data").status_code, 302)
        self.login()
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Good day", dashboard.data)

        row = (datetime(2026, 9, 27, 5, 50, 25, tzinfo=timezone.utc), 31.5, 68, 8.5, 15.2, 12, 32)
        with patch.object(application, "fetch_readings", return_value=([row], 1, None)):
            page = self.client.get("/data?date_range=all&per_page=25")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Timestamp (UTC)", page.data)
            self.assertIn(b"31.5", page.data)
            self.assertIn(b"Sensor Readings", page.data)
            self.assertIn(b"data-custom-select", page.data)
            self.assertIn(b"panel-heading-tools", page.data)
            self.assertIn(b"Export CSV", page.data)
            self.assertNotIn(b"page-heading-actions", page.data)
            self.assertNotIn(b'name="per_page"', page.data)
            self.assertNotIn(b">Apply<", page.data)
            self.assertNotIn(b">Clear<", page.data)
            self.assertIn(b'aria-label="Log out"', page.data)
            self.assertNotIn(b">Logout<", page.data)

            export = self.client.get("/data/export.csv?date_range=all")
            self.assertEqual(export.status_code, 200)
            self.assertIn(b"Timestamp (UTC),Temp", export.data)
            self.assertIn(b"2026-09-27 05:50:25", export.data)

    def test_custom_date_validation(self):
        filters = application.parse_filters({
            "date_range": "custom",
            "start_date": "2026-09-30",
            "end_date": "2026-09-01",
        })
        _, _, error = application.build_where_clause(filters)
        self.assertIsNotNone(error)

    def test_compact_numbered_pagination(self):
        self.assertEqual(application.build_pagination_items(1, 1), [1])
        self.assertEqual(application.build_pagination_items(1, 6), [1, 2, 3, 4])
        self.assertEqual(application.build_pagination_items(4, 6), [3, 4, 5, 6])
        self.assertEqual(application.build_pagination_items(5, 10), [4, 5, 6, 7])

        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 240, None)):
            page = self.client.get("/data?page=5&per_page=25")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'aria-current="page">5</span>', page.data)
            self.assertIn(b"Showing <strong>101\xe2\x80\x93125</strong> of <strong>240</strong>", page.data)

    def test_poppins_is_self_hosted(self):
        font_dir = Path(__file__).parent / "static" / "fonts"
        for name in ("Regular", "Medium", "SemiBold", "Bold"):
            self.assertGreater((font_dir / f"Poppins-{name}.ttf").stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
