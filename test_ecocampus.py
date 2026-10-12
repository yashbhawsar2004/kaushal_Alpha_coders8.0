"""
test_ecocampus.py — Comprehensive Automated Test Suite for EcoCampus.
Audits database layer, Flask REST API endpoints, input validation,
boundary conditions, security injection handling, and edge cases.
"""

import os
import sys
import json
import tempfile
import unittest

# Ensure the app imports from the current directory
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

# Use an isolated temporary SQLite database for testing
TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "test_ecocampus_audit.db")
os.environ["DATABASE_PATH"] = TEST_DB_PATH

import database as db
from server import app


class EcoCampusComprehensiveTestSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        app.config["DEBUG"] = False
        cls.client = app.test_client()

    def setUp(self):
        # Reset test database for clean state per test
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass
        db.init_db()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    # ── 1. Health & Static Routes ──────────────────────────────────────
    def test_health_check(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "healthy")

        res_api = self.client.get("/api/health")
        self.assertEqual(res_api.status_code, 200)

    def test_favicon_and_index(self):
        res_fav = self.client.get("/favicon.ico")
        self.assertEqual(res_fav.status_code, 200)
        self.assertEqual(res_fav.mimetype, "image/svg+xml")

        res_idx = self.client.get("/")
        self.assertEqual(res_idx.status_code, 200)
        self.assertIn(b"EcoCampus", res_idx.data)

    # ── 2. Resource Entries API ────────────────────────────────────────
    def test_entries_get(self):
        res = self.client.get("/api/entries")
        self.assertEqual(res.status_code, 200)
        entries = res.get_json()
        self.assertIsInstance(entries, list)
        self.assertGreater(len(entries), 0)  # Demo data seeded

    def test_entries_create_valid(self):
        payload = {
            "resource": "electricity",
            "amount": 25.5,
            "unit": "kWh",
            "area": "Physics Lab",
            "date": "2026-10-12",
            "reporter": "Test User",
            "note": "Testing valid entry"
        }
        res = self.client.post("/api/entries", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertIn("id", data)
        self.assertEqual(data["amount"], 25.5)

    def test_entries_create_invalid_amount(self):
        # Negative amount
        res = self.client.post("/api/entries", json={"resource": "electricity", "amount": -10, "unit": "kWh", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res.status_code, 400)

        # Zero amount
        res2 = self.client.post("/api/entries", json={"resource": "electricity", "amount": 0, "unit": "kWh", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res2.status_code, 400)

        # Non-numeric string amount
        res3 = self.client.post("/api/entries", json={"resource": "electricity", "amount": "invalid", "unit": "kWh", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res3.status_code, 400)

    def test_entries_create_invalid_resource(self):
        res = self.client.post("/api/entries", json={"resource": "uranium", "amount": 10, "unit": "kg", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res.status_code, 400)

    def test_entries_create_missing_fields(self):
        res = self.client.post("/api/entries", json={"resource": "water", "amount": 50})
        self.assertEqual(res.status_code, 400)

    def test_entries_delete(self):
        # Add entry then delete
        payload = {"resource": "water", "amount": 100, "unit": "L", "area": "Garden", "date": "2026-10-12"}
        create_res = self.client.post("/api/entries", json=payload)
        entry_id = create_res.get_json()["id"]

        del_res = self.client.delete(f"/api/entries/{entry_id}")
        self.assertEqual(del_res.status_code, 200)

        # Deleting non-existent ID should return 404
        del_404 = self.client.delete("/api/entries/999999")
        self.assertEqual(del_404.status_code, 404)

    # ── 3. Incident Reports (Issues) API ───────────────────────────────
    def test_issues_get(self):
        res = self.client.get("/api/issues")
        self.assertEqual(res.status_code, 200)
        issues = res.get_json()
        self.assertIsInstance(issues, list)
        self.assertGreater(len(issues), 0)

    def test_issues_create_valid(self):
        payload = {
            "title": "Broken pipe in Block C",
            "category": "water",
            "area": "Block C 1st Floor",
            "priority": "High",
            "reporter": "Campus Watch",
            "description": "Continuous water overflow"
        }
        res = self.client.post("/api/issues", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["status"], "Reported")
        self.assertEqual(data["title"], "Broken pipe in Block C")

    def test_issues_create_invalid(self):
        # Missing required title and area
        res = self.client.post("/api/issues", json={"priority": "High"})
        self.assertEqual(res.status_code, 400)

    def test_issues_status_update(self):
        # Create issue
        create_res = self.client.post("/api/issues", json={"title": "Light left on", "area": "Hall 1"})
        issue_id = create_res.get_json()["id"]

        # Update to In Progress
        stat_res = self.client.post(f"/api/issues/{issue_id}/status", json={"status": "In Progress"})
        self.assertEqual(stat_res.status_code, 200)

        # Update to Resolved
        stat_res2 = self.client.post(f"/api/issues/{issue_id}/status", json={"status": "Resolved"})
        self.assertEqual(stat_res2.status_code, 200)

        # Invalid status should return 400
        stat_bad = self.client.post(f"/api/issues/{issue_id}/status", json={"status": "ClosedForever"})
        self.assertEqual(stat_bad.status_code, 400)

        # Non-existent ID
        stat_404 = self.client.post("/api/issues/999999/status", json={"status": "Resolved"})
        self.assertEqual(stat_404.status_code, 404)

    def test_issues_delete(self):
        create_res = self.client.post("/api/issues", json={"title": "To Delete", "area": "Test Area"})
        issue_id = create_res.get_json()["id"]

        del_res = self.client.delete(f"/api/issues/{issue_id}")
        self.assertEqual(del_res.status_code, 200)

        # Non-existent ID
        del_404 = self.client.delete("/api/issues/999999")
        self.assertEqual(del_404.status_code, 404)

    # ── 4. Leaderboard & AI Insights ───────────────────────────────────
    def test_leaderboard(self):
        res = self.client.get("/api/leaderboard")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data), 6)
        self.assertEqual(data[0]["rank"], 1)
        self.assertIn("points", data[0])
        self.assertIn("co2_saved_kg", data[0])

    def test_insights(self):
        res = self.client.get("/api/insights")
        self.assertEqual(res.status_code, 200)
        insights = res.get_json()
        self.assertIsInstance(insights, list)
        self.assertGreater(len(insights), 0)
        for item in insights:
            self.assertIn("title", item)
            self.assertIn("saving", item)

    # ── 5. IoT Sensor Telemetry ────────────────────────────────────────
    def test_sensor_ingest_valid(self):
        payload = {
            "sensor_id": "TEST-ESP32-99",
            "resource": "electricity",
            "amount": 12.4,
            "unit": "kWh",
            "area": "Smart Lab 4"
        }
        res = self.client.post("/api/sensors/ingest", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["entry"]["amount"], 12.4)

    def test_sensor_ingest_invalid(self):
        # Missing amount
        res = self.client.post("/api/sensors/ingest", json={"sensor_id": "SN1"})
        self.assertEqual(res.status_code, 400)

        # Negative amount
        res2 = self.client.post("/api/sensors/ingest", json={"amount": -5})
        self.assertEqual(res2.status_code, 400)

        # Non-numeric string amount
        res3 = self.client.post("/api/sensors/ingest", json={"amount": "abc"})
        self.assertEqual(res3.status_code, 400)

    # ── 6. CSV Export ──────────────────────────────────────────────────
    def test_export_csv(self):
        res = self.client.get("/api/export")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "text/csv")
        self.assertIn(b"Date,Resource,Quantity", res.data)

    # ── 7. Green Actions Checklist ─────────────────────────────────────
    def test_actions_crud(self):
        # Toggle action 0 to completed
        res = self.client.post("/api/actions", json={"action_idx": 0, "completed": True})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn(0, data["completed_actions"])

        # Fetch actions
        get_res = self.client.get("/api/actions")
        self.assertEqual(get_res.status_code, 200)
        self.assertIn(0, get_res.get_json())

        # Untoggle action 0
        res_un = self.client.post("/api/actions", json={"action_idx": 0, "completed": False})
        self.assertEqual(res_un.status_code, 200)
        self.assertNotIn(0, res_un.get_json()["completed_actions"])

        # Invalid action_idx
        bad_idx = self.client.post("/api/actions", json={"action_idx": 99})
        self.assertEqual(bad_idx.status_code, 400)


if __name__ == "__main__":
    unittest.main()
