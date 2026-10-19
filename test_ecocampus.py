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
from datetime import date, timedelta

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
        admin_id = db.create_user("test-admin", "Test Administrator", "test-admin-password-2026", role="admin")
        self.client.environ_base["HTTP_X_CSRF_TOKEN"] = "test-csrf-token"
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = admin_id
            session["csrf_token"] = "test-csrf-token"

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

    def test_authentication_roles_ownership_and_csrf(self):
        anonymous = app.test_client()
        self.assertEqual(anonymous.get("/api/entries").status_code, 401)
        self.assertEqual(anonymous.get("/admin/users").status_code, 302)

        csrf = anonymous.get("/api/auth/me").get_json()["csrf_token"]
        rejected = anonymous.post(
            "/api/auth/register",
            json={"username": "new.student", "display_name": "New Student", "password": "userpass1"},
        )
        self.assertEqual(rejected.status_code, 403)
        invalid_passwords = ("", "123456789")
        for invalid_password in invalid_passwords:
            invalid = anonymous.post(
                "/api/auth/register",
                headers={"X-CSRF-Token": csrf},
                json={
                    "username": "new.student",
                    "display_name": "New Student",
                    "password": invalid_password,
                },
            )
            self.assertEqual(invalid.status_code, 400)
        registered = anonymous.post(
            "/api/auth/register",
            headers={"X-CSRF-Token": csrf},
            json={
                "username": "new.student", "display_name": "New Student",
                "password": "user1234", "role": "admin",
                "department": "Restricted Department",
            },
        )
        self.assertEqual(registered.status_code, 201)
        user = registered.get_json()["user"]
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["department"], "")
        self.assertNotIn("password_hash", user)

        user_csrf = registered.get_json()["csrf_token"]
        own_entry = anonymous.post(
            "/api/entries",
            headers={"X-CSRF-Token": user_csrf},
            json={
                "resource": "electricity", "amount": 5, "unit": "kWh",
                "area": "Student Lab", "department": "Restricted Department",
                "date": date.today().isoformat(),
            },
        )
        self.assertEqual(own_entry.status_code, 201)
        self.assertEqual(own_entry.get_json()["department"], "Unassigned")
        self.assertEqual(anonymous.get("/api/entries").get_json()[0]["owner_user_id"], user["id"])
        self.assertEqual(anonymous.get("/api/monitoring/limits").status_code, 403)
        self.assertEqual(
            anonymous.post(
                "/api/monitoring/limits",
                headers={"X-CSRF-Token": user_csrf},
                json={"scope_type": "university"},
            ).status_code,
            403,
        )
        visible_monitoring = anonymous.get("/api/monitoring?resource=electricity").get_json()
        self.assertEqual(visible_monitoring["buildings"], [])
        self.assertEqual(visible_monitoring["departments"], [])

        admin_user = db.get_user_by_id(self.client.get("/api/auth/me").get_json()["user"]["id"])
        self.assertEqual(admin_user["role"], "admin")
        admin_entries = self.client.get("/api/entries").get_json()
        self.assertIn(user["id"], {entry["owner_user_id"] for entry in admin_entries})

    def test_sign_out_clears_session_and_requires_login_again(self):
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertIsNone(self.client.get("/api/auth/me").get_json()["user"])
        self.assertEqual(self.client.get("/api/entries").status_code, 401)

    def test_admin_assigns_department_manager_and_limits_visibility(self):
        user_id = db.create_user("dept.manager", "Department Manager", "manager-password-2026")
        response = self.client.patch(
            f"/api/admin/users/{user_id}",
            json={"role": "manager", "department": "Computer Science & IT", "is_active": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["role"], "manager")

        manager = app.test_client()
        manager.environ_base["HTTP_X_CSRF_TOKEN"] = "manager-csrf"
        with manager.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "manager-csrf"
        own_entry = manager.post(
            "/api/entries",
            json={
                "resource": "water", "amount": 10, "unit": "L", "area": "CS Lab",
                "department": "Other Department", "date": date.today().isoformat(),
            },
        )
        self.assertEqual(own_entry.status_code, 201)
        self.assertEqual(own_entry.get_json()["department"], "Computer Science & IT")
        self.assertEqual(manager.get("/api/entries").get_json()[0]["department"], "Computer Science & IT")
        self.assertEqual(manager.get("/api/admin/users").status_code, 403)
        self.assertEqual(manager.get("/admin/users").status_code, 403)

        outside_issue = self.client.post(
            "/api/issues",
            json={"title": "Other department issue", "area": "Remote Lab", "department": "Other Department"},
        ).get_json()
        own_issue = manager.post(
            "/api/issues",
            json={"title": "CS lab issue", "area": "CS Lab", "department": "Other Department"},
        )
        self.assertEqual(own_issue.status_code, 201)
        self.assertEqual(own_issue.get_json()["department"], "Computer Science & IT")
        self.assertEqual(
            [issue["id"] for issue in manager.get("/api/issues").get_json()],
            [own_issue.get_json()["id"]],
        )
        self.assertEqual(
            manager.patch(
                f"/api/issues/{outside_issue['id']}/status",
                json={"status": "Resolved"},
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/admin/audit-logs").status_code, 200)

    def test_disabled_accounts_cannot_use_existing_sessions_or_login(self):
        user_id = db.create_user("disabled.user", "Disabled User", "pass1234")
        disabled_client = app.test_client()
        with disabled_client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "disabled-csrf"

        response = self.client.patch(
            f"/api/admin/users/{user_id}",
            json={"role": "user", "department": "", "is_active": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(disabled_client.get("/api/entries").status_code, 401)
        login_csrf = disabled_client.get("/api/auth/me").get_json()["csrf_token"]
        login_response = disabled_client.post(
            "/api/auth/login",
            headers={"X-CSRF-Token": login_csrf},
            json={"username": "disabled.user", "password": "pass1234"},
        )
        self.assertEqual(login_response.status_code, 401)

    def test_bootstrap_admin_can_be_provisioned_and_login(self):
        admin_id = db.ensure_bootstrap_admin("bootstrap.root", "pass1234")
        self.assertEqual(db.get_user_by_id(admin_id)["role"], "admin")
        self.assertEqual(
            db.ensure_bootstrap_admin("bootstrap.root", "newpass8"),
            admin_id,
        )

        bootstrap_client = app.test_client()
        login_csrf = bootstrap_client.get("/api/auth/me").get_json()["csrf_token"]
        response = bootstrap_client.post(
            "/api/auth/login",
            headers={"X-CSRF-Token": login_csrf},
            json={"username": "bootstrap.root", "password": "newpass8"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["role"], "admin")

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
        self.assertEqual(data["building"], "Physics Lab")
        self.assertEqual(data["department"], "Unassigned")

    def test_entries_create_invalid_amount(self):
        # Negative amount
        res = self.client.post("/api/entries", json={"resource": "electricity", "amount": -10, "unit": "kWh", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res.status_code, 400)

        # Zero consumption is a valid measured value.
        res2 = self.client.post("/api/entries", json={"resource": "electricity", "amount": 0, "unit": "kWh", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.get_json()["amount"], 0)

        # Non-numeric string amount
        res3 = self.client.post("/api/entries", json={"resource": "electricity", "amount": "invalid", "unit": "kWh", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res3.status_code, 400)

    def test_entries_create_invalid_resource(self):
        res = self.client.post("/api/entries", json={"resource": "uranium", "amount": 10, "unit": "kg", "area": "Lab", "date": "2026-10-12"})
        self.assertEqual(res.status_code, 400)

    def test_entries_create_missing_fields(self):
        res = self.client.post("/api/entries", json={"resource": "water", "amount": 50})
        self.assertEqual(res.status_code, 400)

    def test_monitoring_aggregates_and_classifies_scopes(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        readings = [
            ("North Hall", "Engineering", 10, yesterday),
            ("North Hall", "Engineering", 20, today),
            ("South Hall", "Science", 40, yesterday),
            ("South Hall", "Science", 20, today),
            ("Zero Meter", "Utilities", 30, yesterday),
            ("Zero Meter", "Utilities", 0, today),
            ("Dormitory", "Housing", 5, (date.today() - timedelta(days=30)).isoformat()),
        ]
        for building, department, amount, reading_date in readings:
            db.add_entry({
                "resource": "generalWaste", "amount": amount, "unit": "kg",
                "area": f"{building} Room 1", "building": building,
                "department": department, "date": reading_date,
            })

        for scope_type, scope_name, maximum, under_ratio in [
            ("building", "North Hall", 15, 0.5),
            ("building", "South Hall", 10, 0.8),
            ("university", "University", 35, 0.5),
        ]:
            response = self.client.post(
                "/api/monitoring/limits",
                json={
                    "scope_type": scope_type, "scope_name": scope_name,
                    "resource": "generalWaste", "period": "daily",
                    "max_consumption": maximum, "under_usage_ratio": under_ratio,
                },
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/monitoring?period=daily&resource=generalWaste")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        university = data["university"]
        buildings = {item["name"]: item for item in data["buildings"]}
        departments = {item["name"]: item for item in data["departments"]}
        self.assertEqual(university["actual"], sum(item["actual"] for item in buildings.values()))
        self.assertEqual(university["actual"], sum(item["actual"] for item in departments.values()))
        self.assertEqual(university["actual"], 20 + 20)
        self.assertAlmostEqual(university["average"], 125 / 3, places=2)
        self.assertAlmostEqual(university["average_per_building"], 65 / 4, places=2)
        self.assertEqual(university["limit"], 35)
        self.assertEqual(buildings["North Hall"]["status"], "Over Consumption")
        self.assertEqual(buildings["South Hall"]["status"], "Over Consumption")
        self.assertEqual(buildings["Zero Meter"]["actual"], 0)
        self.assertEqual(buildings["Zero Meter"]["status"], "Under Usage")
        self.assertEqual(departments["Engineering"]["actual"], buildings["North Hall"]["actual"])
        self.assertEqual(departments["Science"]["status"], "Within Limit")
        self.assertEqual(buildings["Dormitory"]["status"], "Missing Data")
        self.assertIn("Outdated", buildings["Dormitory"]["data_quality"])
        alert_keys = [
            (item["scope_type"], item["name"], item["resource"], item["period_key"], item["status"])
            for item in data["alerts"]
        ]
        self.assertEqual(len(alert_keys), len(set(alert_keys)))

    def test_monitoring_weekly_monthly_and_empty_data(self):
        recent = date.today()
        # Use today's date so the latest bucket follows the machine clock.
        entries = [
            (recent.isoformat(), 4),
            (recent.isoformat(), 6),
        ]
        for reading_date, amount in entries:
            db.add_entry({
                "resource": "generalWaste", "amount": amount, "unit": "kg",
                "area": "South Hall Room 1", "building": "South Hall",
                "department": "Science", "date": reading_date,
            })
        weekly = self.client.get("/api/monitoring?period=weekly&resource=generalWaste").get_json()
        monthly = self.client.get("/api/monitoring?period=monthly&resource=generalWaste").get_json()
        self.assertEqual(weekly["university"]["actual"], 10)
        self.assertEqual(monthly["university"]["actual"], 10)
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM entries WHERE resource IN ('paper', 'generalWaste')")
            conn.commit()
        finally:
            conn.close()
        empty = self.client.get("/api/monitoring?period=daily&resource=paper").get_json()
        self.assertIsNone(empty["university"]["average"])
        self.assertEqual(empty["university"]["status"], "Missing Data")
        comparison = self.client.get("/api/monitoring?period=daily&resource=all").get_json()
        compared_resources = {item["resource"]: item for item in comparison["resource_summaries"]}
        self.assertEqual(len(compared_resources), 5)
        self.assertEqual(compared_resources["generalWaste"]["status"], "Missing Data")

    def test_monitoring_normalizes_supported_units_and_rejects_mismatches(self):
        response = self.client.post("/api/entries", json={
            "resource": "water", "amount": 1.5, "unit": "kL",
            "area": "Tank Room", "building": "North Hall",
            "department": "Facilities", "date": date.today().isoformat(),
        })
        self.assertEqual(response.status_code, 201)
        monitoring_response = self.client.get("/api/monitoring?resource=water")
        self.assertEqual(monitoring_response.status_code, 200)
        water = monitoring_response.get_json()
        self.assertEqual(water["university"]["unit"], "L")
        self.assertEqual(water["university"]["actual"], 2090)
        unsupported = self.client.post("/api/entries", json={
            "resource": "water", "amount": 5, "unit": "kWh",
            "area": "Tank Room", "date": date.today().isoformat(),
        })
        self.assertEqual(unsupported.status_code, 400)

    def test_monitoring_rejects_invalid_period_and_protects_limit_updates(self):
        self.assertEqual(self.client.get("/api/monitoring?period=yearly").status_code, 400)
        payload = {
            "scope_type": "building", "scope_name": "North Hall",
            "resource": "electricity", "period": "daily", "max_consumption": 0,
        }
        invalid_limit = self.client.post("/api/monitoring/limits", json=payload)
        self.assertEqual(invalid_limit.status_code, 400)
        anonymous = app.test_client()
        csrf = anonymous.get("/api/auth/me").get_json()["csrf_token"]
        unauthorized = anonymous.post(
            "/api/monitoring/limits",
            headers={"X-CSRF-Token": csrf},
            json={"scope_type": "building", "scope_name": "North Hall", "resource": "electricity", "period": "daily", "max_consumption": 20},
        )
        self.assertEqual(unauthorized.status_code, 401)

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

    def test_legacy_entry_table_migrates_without_losing_readings(self):
        conn = db.get_connection()
        try:
            conn.execute("DROP TABLE entries")
            conn.execute("""
                CREATE TABLE entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource TEXT NOT NULL,
                    amount REAL NOT NULL CHECK(amount > 0),
                    unit TEXT NOT NULL,
                    area TEXT NOT NULL,
                    date TEXT NOT NULL,
                    reporter TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    is_demo INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "INSERT INTO entries (resource, amount, unit, area, date) VALUES (?, ?, ?, ?, ?)",
                ("electricity", 12, "kWh", "Legacy Lab", date.today().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        db.init_db()
        legacy_entry = db.get_all_entries()[0]
        self.assertEqual(legacy_entry["building"], "Legacy Lab")
        self.assertEqual(legacy_entry["department"], "Unassigned")
        zero_entry = db.add_entry({
            "resource": "electricity", "amount": 0, "unit": "kWh",
            "area": "Legacy Lab", "date": date.today().isoformat(),
        })
        self.assertEqual(zero_entry["amount"], 0)

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

    def test_sensor_ingest_requires_admin_session_and_csrf(self):
        anonymous = app.test_client()
        payload = {
            "sensor_id": "EXTERNAL-ESP32-1",
            "resource": "electricity",
            "amount": 1.5,
            "unit": "kWh",
            "area": "External Sensor Lab",
        }
        self.assertEqual(anonymous.post("/api/sensors/ingest", json=payload).status_code, 401)

        user_id = db.create_user("sensor.user", "Sensor User", "sensor-user-password-2026")
        user_client = app.test_client()
        with user_client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "sensor-user-csrf"
        self.assertEqual(
            user_client.post(
                "/api/sensors/ingest",
                headers={"X-CSRF-Token": "sensor-user-csrf"},
                json=payload,
            ).status_code,
            403,
        )

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
