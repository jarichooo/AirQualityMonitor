import csv
import io
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

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
        with patch.object(application, "get_db_connection") as connect:
            connect.return_value.cursor.return_value.__enter__.return_value.fetchone.side_effect = [(0, None), None, (None, None, None, 0) * 8]
            connect.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
            dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Good day", dashboard.data)

        row = (datetime(2026, 9, 27, 5, 50, 25, tzinfo=timezone.utc), 31.5, 68, 420, 8.0, 15.2, 22.0, 12, 32)
        with patch.object(application, "fetch_readings", return_value=([row], 1, None)):
            page = self.client.get("/data?date_range=all&per_page=25")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Timestamp (UTC)", page.data)
            self.assertIn(b"31.5", page.data)
            self.assertIn(b"CO2 (ppm)", page.data)
            self.assertIn(b"420", page.data)
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
            csv_rows = list(csv.reader(io.StringIO(export.data.decode())))
            self.assertEqual(csv_rows[0][-5:], ["PM1.0 (µg/m³)", "PM2.5 (µg/m³)", "PM10 (µg/m³)", "MQ135 (raw)", "MQ137 (raw)"])
            self.assertEqual(csv_rows[1][-5:], ["8.0", "15.2", "22.0", "12", "32"])
            self.assertIn(b"MQ135 (raw)", page.data)
            self.assertNotIn(b"MQ135 (ppm)", page.data)

    def test_custom_date_validation(self):
        filters = application.parse_filters({
            "date_range": "custom",
            "start_date": "2026-09-30",
            "end_date": "2026-09-01",
        })
        _, _, error = application.build_where_clause(filters)
        self.assertIsNotNone(error)

    def test_collection_summary_empty_populated_and_unavailable(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.login()
        for result, expected in (((0, None), b"No readings yet"),
                                 ((3, datetime(2026, 10, 1, 12)), b"<strong>3</strong> stored readings")):
            with patch.object(application, "get_db_connection") as connect:
                cursor = connect.return_value.cursor.return_value.__enter__.return_value
                cursor.fetchone.side_effect = [result, None, (None, None, None, 0) * 8]
                cursor.fetchall.return_value = []
                page = self.client.get("/dashboard")
                self.assertEqual(page.status_code, 200)
                self.assertIn(expected, page.data)
                connect.return_value.close.assert_called_once()
        with patch.object(application, "get_db_connection", side_effect=application.psycopg2.OperationalError):
            self.assertIn(b"Collection status is unavailable", self.client.get("/dashboard").data)

    def test_empty_data_and_database_failure(self):
        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            self.assertIn(b"No readings yet", self.client.get("/data").data)
            export = self.client.get("/data/export.csv")
            self.assertEqual(len(export.data.decode().splitlines()), 1)
        with patch.object(application, "fetch_readings", side_effect=application.psycopg2.OperationalError):
            self.assertIn(b"Sensor data is unavailable", self.client.get("/data").data)
            self.assertEqual(self.client.get("/data/export.csv").status_code, 503)

    def test_missing_login_fields_and_export_authentication(self):
        self.assertEqual(self.client.post("/login", data={}).status_code, 400)
        self.assertEqual(self.client.get("/data/export.csv").status_code, 302)

    def test_root_always_opens_login_and_login_redirects_to_dashboard(self):
        for authenticated in (False, True):
            if authenticated:
                self.login()
            response = self.client.get("/", follow_redirects=True)
            self.assertEqual(response.request.path, "/login")
            self.assertIn(b'name="password"', response.data)
        with patch.object(application, "get_db_connection") as connect, patch.object(application, "check_password_hash", return_value=True):
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = {"password_hash": "hash", "role": "admin"}
            response = self.client.post("/login", data={"username": "admin", "password": "password"})
            self.assertEqual(response.location, "/dashboard")
        self.client.get("/logout")
        self.assertEqual(self.client.get("/dashboard").location, "/login")

    def test_analytics_and_missing_hour_gaps(self):
        self.login()
        start = datetime.now(timezone.utc) - timedelta(hours=24)
        series = [(start, 30), (start + timedelta(hours=1), 32), (start + timedelta(hours=3), 31)]
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(3, start), (start, 30) + (None,) * 7,
                                          (30, 31, 32, 3) + (None, None, None, 0) * 7]
            cursor.fetchall.return_value = series
            page = self.client.get("/dashboard?sensor=bad")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"30.00", page.data)
            self.assertIn(b"31.00", page.data)
            self.assertIn(b"32.00", page.data)
            self.assertIn(b"2-hour forecast", page.data)
            self.assertIn(b"Awaiting integration", page.data)
            self.assertIn(b"<polyline", page.data)
            self.assertIn("AVG(temperature_c)", cursor.execute.call_args.args[0])
        trend = application.build_trend(series, start)
        self.assertEqual(len(trend["segments"]), 2)
        self.assertEqual(len(trend["points"]), 3)
        self.assertFalse(application.build_trend([(start, None)], start)["points"])

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
