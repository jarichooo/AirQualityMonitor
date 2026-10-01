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
            self.assertIn(b"profile-identity", page.data)
            self.assertNotIn(b"profile-button", page.data)
            self.assertNotIn(b"profile-popover", page.data)

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

    def test_live_data_is_authenticated_and_forces_first_page(self):
        self.assertEqual(self.client.get("/data/live").status_code, 302)
        self.login()
        row = (datetime(2026, 9, 27, 5, 50, 25, tzinfo=timezone.utc), 31.5, 68, 8.5, 15.2, 12, 32)
        with patch.object(application, "fetch_readings", return_value=([row], 26, None)) as fetch:
            response = self.client.get("/data/live?page=7&per_page=100&date_range=7d&q=31.5")

        self.assertEqual(response.status_code, 200)
        filters = fetch.call_args.args[0]
        self.assertEqual(filters["page"], 1)
        self.assertEqual(filters["per_page"], 25)
        self.assertEqual(filters["date_range"], "7d")
        self.assertEqual(filters["q"], "31.5")
        payload = response.get_json()
        self.assertIn("2026-09-27 05:50:25", payload["rows_html"])
        self.assertIn('aria-current="page">1</span>', payload["pagination_html"])
        self.assertEqual(payload["record_count"], "26 matching records")

    def test_empty_out_of_range_data_page_redirects_to_first_page(self):
        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            response = self.client.get("/data?page=6&date_range=7d&q=no-match")

        self.assertEqual(response.status_code, 302)
        self.assertIn("page=1", response.headers["Location"])
        self.assertIn("date_range=7d", response.headers["Location"])
        self.assertIn("q=no-match", response.headers["Location"])

        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get(response.headers["Location"])

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Showing <strong>0\xe2\x80\x930</strong> of <strong>0</strong>", page.data)
        self.assertIn(b'aria-current="page">1</span>', page.data)
        self.assertIn(b'aria-disabled="true">Previous</span>', page.data)
        self.assertIn(b'aria-disabled="true">Next</span>', page.data)

    def test_data_page_preserves_database_and_filter_errors_on_high_page(self):
        self.login()
        with patch.object(application, "fetch_readings", side_effect=application.psycopg2.Error("down")):
            database_page = self.client.get("/data?page=6&q=no-match")

        self.assertEqual(database_page.status_code, 200)
        self.assertNotIn(b"Location", database_page.data)
        self.assertIn(b"Sensor data is unavailable", database_page.data)

        filter_page = self.client.get(
            "/data?page=6&date_range=custom&start_date=2026-09-30&end_date=2026-09-01"
        )
        self.assertEqual(filter_page.status_code, 200)
        self.assertIn(b"Check the selected dates", filter_page.data)

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

    def test_dashboard_range_and_threshold_contract(self):
        self.assertEqual(application.parse_dashboard_range("6h"), "6h")
        self.assertEqual(application.parse_dashboard_range("invalid"), "24h")
        self.assertEqual(application.dashboard_metric_status("nh3_ppm", 25), "within_range")
        self.assertEqual(application.dashboard_metric_status("nh3_ppm", 25.01), "attention")
        self.assertEqual(application.dashboard_metric_status("co2_ppm", 900), "unclassified")

    def test_dashboard_route_and_live_endpoint(self):
        self.assertEqual(self.client.get("/dashboard/live").status_code, 302)
        self.login()
        payload = application.dashboard_empty_payload("6h")
        with patch.object(application, "build_dashboard_payload", return_value=payload) as build:
            page = self.client.get("/?range=6h")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Environmental Trend", page.data)
            self.assertIn(b"Environmental Assessment", page.data)
            self.assertIn(b"AI Forecast", page.data)
            self.assertIn(b'data-metric="nh3_ppm"', page.data)
            self.assertIn(b'id="dashboard-initial-data"', page.data)

            live = self.client.get("/dashboard/live?range=6h")
            self.assertEqual(live.status_code, 200)
            self.assertEqual(live.get_json()["range"], "6h")
            self.assertEqual(build.call_count, 2)
            self.assertEqual(build.call_args_list[0].args, ("6h",))
            self.assertEqual(build.call_args_list[1].args, ("6h",))

    def test_dashboard_database_failure_renders_controlled_error(self):
        self.login()
        with patch.object(application, "build_dashboard_payload", side_effect=application.psycopg2.Error("down")):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Dashboard data could not be loaded", response.data)
        self.assertIn(b"Retry", response.data)

    def test_preview_mode_bypasses_login_only_when_enabled(self):
        with patch.object(application, "PREVIEW_MODE", True), patch.object(
            application, "fetch_readings", side_effect=application.psycopg2.Error()
        ):
            response = self.client.get("/data")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sensor Readings", response.data)

    def test_preview_dataset_powers_data_and_live_refresh(self):
        with patch.object(application, "PREVIEW_MODE", True):
            filters = application.parse_filters({"date_range": "all", "page": "1", "per_page": "25"})
            readings, total, error = application.fetch_readings(filters)
            self.assertIsNone(error)
            self.assertEqual(total, 100)
            self.assertEqual(len(readings), 25)

            live = self.client.get("/data/live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["record_count"], "100 matching records")

    def test_preview_dataset_powers_dashboard_and_live_refresh(self):
        with patch.object(application, "PREVIEW_MODE", True):
            payload = application.build_dashboard_payload("24h")
            self.assertEqual(payload["latest"]["overall_status"], "within_range")
            self.assertEqual(payload["latest"]["metrics"]["co2_ppm"]["value"], 820.0)
            self.assertEqual(payload["summary"]["nh3_ppm"]["exceedance_count"], 0)

            response = self.client.get("/dashboard/live?range=24h")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["range"], "24h")
        self.assertGreater(len(response.get_json()["series"]["timestamps"]), 0)


if __name__ == "__main__":
    unittest.main()
