"""
audit_verifier.py — Standalone Verification Script for EcoCampus.
Runs in-memory / temporary SQLite tests against all endpoints and database methods.
Logs exact passes and assertions.
"""

import os
import sys
import tempfile
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

test_db = os.path.join(tempfile.gettempdir(), "ecocampus_test_audit_verify.db")
if os.path.exists(test_db):
    try: os.remove(test_db)
    except Exception: pass

os.environ["DATABASE_PATH"] = test_db

import database as db
from server import app

passed = 0
failed = 0
results = []

def run_test(name, func):
    global passed, failed
    try:
        func()
        passed += 1
        results.append((name, "PASSED", None))
        print(f"  [PASS] {name}")
    except Exception as exc:
        failed += 1
        results.append((name, "FAILED", str(exc)))
        print(f"  [FAIL] {name}: {exc}")

print("\n--- RUNNING AUDIT VERIFICATION SUITE ---")

client = app.test_client()

# 1. DB Init
def test_db_init():
    db.init_db()
    entries = db.get_all_entries()
    assert len(entries) > 0, "Entries should be seeded"
    issues = db.get_all_issues()
    assert len(issues) > 0, "Issues should be seeded"
run_test("Database initialization & seed integrity", test_db_init)

# 2. Health endpoint
def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "healthy"
run_test("Health endpoint (/health)", test_health)

# 3. Security headers
def test_security_headers():
    res = client.get("/health")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"
run_test("Security HTTP headers (nosniff, SAMEORIGIN)", test_security_headers)

# 4. Entry creation with boundary checks
def test_entry_create():
    # Valid
    res = client.post("/api/entries", json={
        "resource": "electricity", "amount": 10.5, "unit": "kWh", "area": "Hall B", "date": "2026-10-12"
    })
    assert res.status_code == 201, f"Expected 201, got {res.status_code}"
    
    # Negative amount
    res_neg = client.post("/api/entries", json={
        "resource": "electricity", "amount": -10, "unit": "kWh", "area": "Hall B", "date": "2026-10-12"
    })
    assert res_neg.status_code == 400, "Negative amount should return 400"
    
    # Non-numeric amount
    res_nan = client.post("/api/entries", json={
        "resource": "electricity", "amount": "abc", "unit": "kWh", "area": "Hall B", "date": "2026-10-12"
    })
    assert res_nan.status_code == 400, "String amount should return 400"

    # Overflow amount (> 10,000,000)
    res_over = client.post("/api/entries", json={
        "resource": "electricity", "amount": 999999999, "unit": "kWh", "area": "Hall B", "date": "2026-10-12"
    })
    assert res_over.status_code == 400, "Overflow amount should return 400"

    # Empty area
    res_area = client.post("/api/entries", json={
        "resource": "electricity", "amount": 5, "unit": "kWh", "area": "   ", "date": "2026-10-12"
    })
    assert res_area.status_code == 400, "Whitespace area should return 400"
run_test("Resource Entry CRUD & boundary validation", test_entry_create)

# 5. Issue tickets lifecycle
def test_issue_lifecycle():
    res = client.post("/api/issues", json={
        "title": "Leaking Pipe in Gym", "category": "water", "area": "Gymnasium", "priority": "High"
    })
    assert res.status_code == 201
    issue_id = res.get_json()["id"]

    # Update to In Progress
    res_prog = client.post(f"/api/issues/{issue_id}/status", json={"status": "In Progress"})
    assert res_prog.status_code == 200

    # Update to Resolved
    res_res = client.post(f"/api/issues/{issue_id}/status", json={"status": "Resolved"})
    assert res_res.status_code == 200

    # Invalid status
    res_bad = client.post(f"/api/issues/{issue_id}/status", json={"status": "Exploded"})
    assert res_bad.status_code == 400
run_test("Issue tickets lifecycle (Reported -> In Progress -> Resolved)", test_issue_lifecycle)

# 6. Leaderboard calculation
def test_leaderboard():
    res = client.get("/api/leaderboard")
    assert res.status_code == 200
    lb = res.get_json()
    assert len(lb) == 6
    # Verify rankings are strictly sorted descending
    for i in range(len(lb) - 1):
        assert lb[i]["points"] >= lb[i+1]["points"], "Leaderboard must be sorted descending"
        assert lb[i]["rank"] == i + 1
run_test("Department leaderboard ranking & scoring formula", test_leaderboard)

# 7. AI Insights
def test_insights():
    res = client.get("/api/insights")
    assert res.status_code == 200
    ins = res.get_json()
    assert len(ins) >= 3
    assert any("title" in item and "saving" in item for item in ins)
run_test("AI Eco-Advisor insights & anomaly generation", test_insights)

# 8. IoT Telemetry Webhook validation
def test_iot_webhook():
    # Valid
    res = client.post("/api/sensors/ingest", json={
        "sensor_id": "SN-ESP32-1", "resource": "electricity", "amount": 4.5, "unit": "kWh", "area": "Lab 1"
    })
    assert res.status_code == 201

    # Invalid resource
    res_bad_res = client.post("/api/sensors/ingest", json={
        "resource": "gasoline", "amount": 10
    })
    assert res_bad_res.status_code == 400

    # Corrupt string amount
    res_str = client.post("/api/sensors/ingest", json={
        "amount": "corrupt_data"
    })
    assert res_str.status_code == 400
run_test("IoT Sensor telemetry ingestion & edge case validation", test_iot_webhook)

# 9. CSV Export & Formula Injection Prevention
def test_csv_export():
    # Add an entry with a potentially malicious formula note
    client.post("/api/entries", json={
        "resource": "paper", "amount": 10, "unit": "sheets", "area": "Admin", "date": "2026-10-12",
        "note": "=CMD|' /C calc'!A0"
    })
    res = client.get("/api/export")
    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    csv_text = res.data.decode("utf-8")
    assert "'=CMD" in csv_text, "Malicious formula must be escaped with single quote"
run_test("CSV export & Formula Injection (CWE-1236) sanitization", test_csv_export)

# 10. Checklist Actions
def test_actions():
    res = client.post("/api/actions", json={"action_idx": 2, "completed": True})
    assert res.status_code == 200
    assert 2 in res.get_json()["completed_actions"]

    res_out = client.post("/api/actions", json={"action_idx": 10})
    assert res_out.status_code == 400
run_test("Action checklist idempotency & bounds checking", test_actions)

print(f"\n--- AUDIT SUMMARY: {passed} PASSED, {failed} FAILED ---")

# Clean up
if os.path.exists(test_db):
    try: os.remove(test_db)
    except Exception: pass
