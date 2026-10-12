"""
server.py — Production-Ready Flask backend for EcoCampus.
Serves the responsive dashboard, exposes REST API endpoints with:
- Strict input validation & sanitization (mitigating DoS & overflow edge cases)
- Mitigation against CSV formula injection (OWASP/CWE-1236)
- Security HTTP headers (X-Frame-Options, X-Content-Type-Options, etc.)
- Resource tracking, incident reporting, leaderboard, AI advisor, and IoT telemetry ingestion.
"""

import os
import csv
import io
import math
import logging
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import database as db

# ── Logging configuration ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ecocampus")

# ── App initialization ────────────────────────────────────────────────
app = Flask(__name__, static_folder=".", static_url_path="")

cors_origin = os.getenv("CORS_ORIGINS", "*")
CORS(app, resources={r"/*": {"origins": cors_origin}})

# Ensure database tables exist upon server startup
with app.app_context():
    try:
        db.init_db()
        logger.info("Database initialized successfully with all tables and indexes.")
    except Exception as exc:
        logger.error(f"Database initialization warning: {exc}")


# ── Security HTTP Response Headers ────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ── Health check ──────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health_check():
    """Liveness probe for container and cloud deployments."""
    return jsonify({
        "status": "healthy",
        "service": "ecocampus-mvp",
        "version": "2.1.0"
    }), 200


# ── Frontend & static routes ──────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/favicon.ico")
def favicon():
    svg_icon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<text y=".9em" font-size="90">🌿</text>'
        '</svg>'
    )
    return Response(svg_icon, mimetype="image/svg+xml")


ALLOWED_RESOURCES = ["electricity", "water", "paper", "waste", "generalWaste"]
ALLOWED_PRIORITIES = ["Low", "Medium", "High"]
ALLOWED_CATEGORIES = ["water", "electricity", "waste", "other"]
ALLOWED_STATUSES = ["Reported", "In Progress", "Resolved"]


# ── REST API: Resource entries ─────────────────────────────────────────
@app.route("/api/entries", methods=["GET"])
def list_entries():
    """Return all resource entries (sorted newest first)."""
    try:
        entries = db.get_all_entries()
        return jsonify(entries), 200
    except Exception as exc:
        logger.error(f"Error fetching entries: {exc}")
        return jsonify({"error": "Failed to fetch entries"}), 500


@app.route("/api/entries", methods=["POST"])
def create_entry():
    """Create a new resource entry with strict boundary checks."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid or missing JSON payload."}), 400

    resource = str(data.get("resource", "")).strip()
    area = str(data.get("area", "")).strip()
    unit = str(data.get("unit", "")).strip()
    date_str = str(data.get("date", "")).strip()
    reporter = str(data.get("reporter", "")).strip()[:80]
    note = str(data.get("note", "")).strip()[:200]

    if not resource or not area or not unit or not date_str:
        return jsonify({"error": "Fields 'resource', 'amount', 'unit', 'area', and 'date' are required."}), 400

    if resource not in ALLOWED_RESOURCES:
        return jsonify({"error": f"Invalid resource type: '{resource}'. Allowed: {', '.join(ALLOWED_RESOURCES)}"}), 400

    raw_amount = data.get("amount")
    try:
        amount = float(raw_amount)
    except (ValueError, TypeError):
        return jsonify({"error": "Amount must be a valid numeric value."}), 400

    if math.isnan(amount) or math.isinf(amount) or amount <= 0:
        return jsonify({"error": "Amount must be a positive finite number greater than 0."}), 400

    if amount > 10_000_000:
        return jsonify({"error": "Amount exceeds maximum allowable threshold (10,000,000)."}), 400

    payload = {
        "resource": resource,
        "amount": round(amount, 3),
        "unit": unit[:20],
        "area": area[:100],
        "date": date_str[:10],
        "reporter": reporter,
        "note": note,
        "is_demo": bool(data.get("is_demo", False))
    }

    try:
        entry = db.add_entry(payload)
        return jsonify(entry), 201
    except Exception as exc:
        logger.error(f"Error adding entry: {exc}")
        return jsonify({"error": "Database error while saving resource entry."}), 500


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def remove_entry(entry_id):
    """Delete a resource entry by ID."""
    try:
        if db.delete_entry(entry_id):
            return jsonify({"ok": True, "message": f"Entry {entry_id} deleted."}), 200
        return jsonify({"error": f"Entry with ID {entry_id} not found."}), 404
    except Exception as exc:
        logger.error(f"Error deleting entry {entry_id}: {exc}")
        return jsonify({"error": "Failed to delete entry."}), 500


# ── REST API: Totals ───────────────────────────────────────────────────
@app.route("/api/totals", methods=["GET"])
def totals():
    """Return aggregated sums per resource type."""
    try:
        return jsonify(db.get_totals()), 200
    except Exception as exc:
        logger.error(f"Error computing totals: {exc}")
        return jsonify({"error": "Failed to compute totals."}), 500


# ── REST API: Green actions ────────────────────────────────────────────
@app.route("/api/actions", methods=["GET"])
def list_actions():
    """Return completed action indices."""
    try:
        return jsonify(db.get_completed_actions()), 200
    except Exception as exc:
        logger.error(f"Error fetching actions: {exc}")
        return jsonify({"error": "Failed to fetch green actions."}), 500


@app.route("/api/actions", methods=["POST"])
def update_action():
    """Toggle a green action checklist item."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request body."}), 400

    idx = data.get("action_idx")
    completed = bool(data.get("completed", True))

    if idx is None or not isinstance(idx, int) or idx < 0 or idx > 4:
        return jsonify({"error": "Invalid action_idx. Must be an integer between 0 and 4."}), 400

    try:
        db.set_action(idx, completed)
        return jsonify({"ok": True, "completed_actions": db.get_completed_actions()}), 200
    except Exception as exc:
        logger.error(f"Error updating action {idx}: {exc}")
        return jsonify({"error": "Failed to update action."}), 500


# ── REST API: Incident Issue Tickets ──────────────────────────────────
@app.route("/api/issues", methods=["GET"])
def list_issues():
    """List all reported campus environmental incidents / leaks."""
    try:
        return jsonify(db.get_all_issues()), 200
    except Exception as exc:
        logger.error(f"Error fetching issues: {exc}")
        return jsonify({"error": "Failed to fetch issues."}), 500


@app.route("/api/issues", methods=["POST"])
def create_issue():
    """Report an incident (e.g., leaking tap, AC left on)."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body provided."}), 400

    title = str(data.get("title", "")).strip()
    area = str(data.get("area", "")).strip()
    category = str(data.get("category", "other")).strip()
    priority = str(data.get("priority", "Medium")).strip()
    reporter = str(data.get("reporter", "")).strip() or "Student Volunteer"
    description = str(data.get("description", "")).strip()

    if not title or not area:
        return jsonify({"error": "Both 'title' and 'area' are required non-empty fields."}), 400

    if category not in ALLOWED_CATEGORIES:
        category = "other"

    if priority not in ALLOWED_PRIORITIES:
        priority = "Medium"

    payload = {
        "title": title[:120],
        "area": area[:100],
        "category": category,
        "priority": priority,
        "reporter": reporter[:80],
        "description": description[:300]
    }

    try:
        issue = db.add_issue(payload)
        return jsonify(issue), 201
    except Exception as exc:
        logger.error(f"Error creating issue: {exc}")
        return jsonify({"error": "Failed to create issue ticket."}), 500


@app.route("/api/issues/<int:issue_id>/status", methods=["POST", "PATCH"])
def update_issue_status(issue_id):
    """Change issue lifecycle status: Reported -> In Progress -> Resolved."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON request payload."}), 400

    status = data.get("status")
    if status not in ALLOWED_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(ALLOWED_STATUSES)}."}), 400

    try:
        if db.update_issue_status(issue_id, status):
            return jsonify({"ok": True, "message": f"Issue status updated to {status}."}), 200
        return jsonify({"error": f"Issue with ID {issue_id} not found."}), 404
    except Exception as exc:
        logger.error(f"Error updating issue status: {exc}")
        return jsonify({"error": "Failed to update status."}), 500


@app.route("/api/issues/<int:issue_id>", methods=["DELETE"])
def remove_issue(issue_id):
    """Delete an issue ticket."""
    try:
        if db.delete_issue(issue_id):
            return jsonify({"ok": True, "message": f"Issue {issue_id} deleted."}), 200
        return jsonify({"error": f"Issue with ID {issue_id} not found."}), 404
    except Exception as exc:
        logger.error(f"Error deleting issue: {exc}")
        return jsonify({"error": "Failed to delete issue."}), 500


# ── REST API: Leaderboard & AI Insights ───────────────────────────────
@app.route("/api/leaderboard", methods=["GET"])
def get_leaderboard():
    """Return departmental rankings and eco-points."""
    try:
        return jsonify(db.get_leaderboard()), 200
    except Exception as exc:
        logger.error(f"Error generating leaderboard: {exc}")
        return jsonify({"error": "Failed to generate leaderboard."}), 500


@app.route("/api/insights", methods=["GET"])
def get_insights():
    """Return AI Eco-Advisor recommendations, spike detection, and savings."""
    try:
        return jsonify(db.get_insights()), 200
    except Exception as exc:
        logger.error(f"Error computing insights: {exc}")
        return jsonify({"error": "Failed to compute insights."}), 500


# ── REST API: IoT Telemetry Webhook Endpoint ──────────────────────────
@app.route("/api/sensors/ingest", methods=["POST"])
def sensor_ingest():
    """
    Webhook endpoint for smart energy/water meters or microcontrollers.
    Validates payload against corrupt values, invalid resources, and overflows.
    """
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON sensor payload."}), 400

    resource = str(data.get("resource", "electricity")).strip()
    if resource not in ALLOWED_RESOURCES:
        return jsonify({"error": f"Invalid resource: '{resource}'. Allowed: {', '.join(ALLOWED_RESOURCES)}"}), 400

    raw_amount = data.get("amount")
    try:
        amount = float(raw_amount)
    except (ValueError, TypeError):
        return jsonify({"error": "Telemetry 'amount' must be a numeric value."}), 400

    if math.isnan(amount) or math.isinf(amount) or amount <= 0:
        return jsonify({"error": "Telemetry 'amount' must be a positive finite number."}), 400

    if amount > 10_000_000:
        return jsonify({"error": "Telemetry 'amount' exceeds maximum allowable threshold."}), 400

    unit = str(data.get("unit", "kWh")).strip()[:20]
    area = str(data.get("area", "Smart Meter Zone")).strip()[:100]
    sensor_id = str(data.get("sensor_id", "IoT-Sensor")).strip()[:50]

    from datetime import date
    entry_payload = {
        "resource": resource,
        "amount": round(amount, 3),
        "unit": unit,
        "area": area,
        "date": date.today().isoformat(),
        "reporter": f"IoT Meter ({sensor_id})",
        "note": f"Automated reading received via /api/sensors/ingest",
        "is_demo": False
    }

    try:
        saved = db.add_entry(entry_payload)
        logger.info(f"Sensor reading ingested from {sensor_id}: {amount} {unit} at {area}")
        return jsonify({"ok": True, "message": "Telemetry reading ingested.", "entry": saved}), 201
    except Exception as exc:
        logger.error(f"Failed to ingest sensor data: {exc}")
        return jsonify({"error": "Database error while ingesting sensor telemetry."}), 500


# ── REST API: CSV export (Mitigating Formula Injection) ────────────────
LABELS = {
    "electricity": "Electricity",
    "water": "Water",
    "paper": "Paper",
    "waste": "Waste recycled",
    "generalWaste": "General waste",
}


def sanitize_csv_cell(value):
    """
    Mitigates CSV Formula Injection (CWE-1236 / OWASP).
    Prepends a single quote if the string begins with =, +, -, @, or tab/return.
    """
    s = str(value) if value is not None else ""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@app.route("/api/export", methods=["GET"])
def export_csv():
    """Download records in clean RFC 4180 CSV format with formula injection sanitization."""
    try:
        entries = db.get_all_entries()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Date", "Resource", "Quantity", "Unit", "Area", "Logged by", "Note", "Sample data"])
        for e in entries:
            writer.writerow([
                sanitize_csv_cell(e["date"]),
                sanitize_csv_cell(LABELS.get(e["resource"], e["resource"])),
                e["amount"],
                sanitize_csv_cell(e["unit"]),
                sanitize_csv_cell(e["area"]),
                sanitize_csv_cell(e.get("reporter", "")),
                sanitize_csv_cell(e.get("note", "")),
                "Yes" if e.get("is_demo") else "No",
            ])
        output = buf.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=ecocampus-resource-records.csv"},
        )
    except Exception as exc:
        logger.error(f"Error exporting CSV: {exc}")
        return jsonify({"error": "Failed to generate CSV export."}), 500


# ── Error handlers ────────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Bad request"}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ── Execution entry point ─────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5501))
    debug = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true")

    logger.info(f"🌿 Starting EcoCampus server on http://{host}:{port} (debug={debug})")
    app.run(host=host, port=port, debug=debug)
