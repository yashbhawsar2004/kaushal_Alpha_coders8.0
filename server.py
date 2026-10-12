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
import json
import hmac
import re
import secrets
from datetime import date
from flask import Flask, g, jsonify, request, send_from_directory, Response, session, redirect
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

import database as db

# ── Logging configuration ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ecocampus")

# ── App initialization ────────────────────────────────────────────────
app = Flask(__name__, static_folder=".", static_url_path="")
app.config.update(
    SECRET_KEY=os.getenv("SESSION_SECRET_KEY") or db.get_or_create_session_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true"),
)
if not os.getenv("SESSION_SECRET_KEY"):
    logger.warning("SESSION_SECRET_KEY is not set; using a persistent database-backed key. Configure it for production.")

cors_origin = os.getenv("CORS_ORIGINS", "*")
CORS(app, resources={r"/*": {"origins": cors_origin}})

# Ensure database tables exist upon server startup
with app.app_context():
    try:
        db.init_db()
        logger.info("Database initialized successfully with all tables and indexes.")
        bootstrap_username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()
        bootstrap_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", bootstrap_username) and 1 <= len(bootstrap_password) <= 8:
            db.ensure_bootstrap_admin(bootstrap_username, bootstrap_password)
        else:
            logger.warning("Bootstrap administrator requires a valid username and a 1-8 character password.")
    except Exception as exc:
        logger.error(f"Database initialization warning: {exc}")


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/csrf",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/me",
}


@app.before_request
def load_authenticated_user():
    g.current_user = None
    user_id = session.get("user_id")
    if user_id is not None:
        g.current_user = db.get_user_by_id(user_id)
        if g.current_user is None or not g.current_user["is_active"]:
            session.clear()
            g.current_user = None

    if request.path.startswith("/api/"):
        if request.path not in PUBLIC_API_PATHS and g.current_user is None:
            return jsonify({"error": "Authentication is required."}), 401
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            expected_token = session.get("csrf_token", "")
            provided_token = request.headers.get("X-CSRF-Token", "")
            if not expected_token or not hmac.compare_digest(provided_token, expected_token):
                return jsonify({"error": "A valid CSRF token is required."}), 403

    if request.path.startswith("/admin"):
        if g.current_user is None:
            return redirect("/login")
        if g.current_user["role"] != "admin":
            return jsonify({"error": "Administrator access is required."}), 403
    elif request.path.startswith("/user") and g.current_user is None:
        return redirect("/login")


def role_required(*roles):
    user = getattr(g, "current_user", None)
    if user is None:
        return jsonify({"error": "Authentication is required."}), 401
    if user["role"] not in roles:
        return jsonify({"error": "You do not have permission to perform this action."}), 403
    return None


def assigned_departments(user):
    if user["role"] == "admin":
        return None
    if user["role"] == "manager" and user["department"]:
        return [user["department"]]
    return []


def bootstrap_admin_configured():
    return (
        bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()))
        and 1 <= len(os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")) <= 8
    )


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


@app.route("/login")
@app.route("/register")
@app.route("/user")
@app.route("/user/<path:panel_path>")
@app.route("/admin")
@app.route("/admin/<path:panel_path>")
def application_panel(panel_path=""):
    if request.path.startswith("/admin") and (
        g.current_user is None or g.current_user["role"] != "admin"
    ):
        return redirect("/login") if g.current_user is None else (jsonify({"error": "Administrator access is required."}), 403)
    if request.path.startswith("/user") and g.current_user is None:
        return redirect("/login")
    return send_from_directory(".", "index.html")


@app.route("/api/auth/csrf", methods=["GET"])
def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return jsonify({"csrf_token": token}), 200


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return jsonify({
        "user": g.current_user,
        "csrf_token": token,
        "registration_enabled": True,
        "admin_configured": bootstrap_admin_configured(),
    }), 200


@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON registration payload is required."}), 400
    username = str(data.get("username", "")).strip()
    display_name = str(data.get("display_name", "")).strip()
    password = data.get("password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        return jsonify({"error": "Username must be 3-40 characters using letters, numbers, dot, underscore, or hyphen."}), 400
    if not display_name or len(display_name) > 80:
        return jsonify({"error": "Display name must contain 1-80 characters."}), 400
    if not isinstance(password, str) or not 1 <= len(password) <= 8:
        return jsonify({"error": "Password must be between 1 and 8 characters."}), 400
    try:
        user_id = db.create_user(username, display_name, password)
        user = db.get_user_by_id(user_id)
        session.clear()
        session["user_id"] = user_id
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"user": user, "csrf_token": session["csrf_token"]}), 201
    except Exception as exc:
        if "UNIQUE constraint failed: users.username" in str(exc):
            return jsonify({"error": "That username is already registered."}), 409
        logger.error("Error registering account: %s", exc)
        return jsonify({"error": "Failed to create account."}), 500


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON login payload is required."}), 400
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    user = db.get_user_by_username(username)
    if (
        user is None
        or not user["is_active"]
        or not isinstance(password, str)
        or not check_password_hash(user["password_hash"], password)
    ):
        return jsonify({"error": "Invalid username or password."}), 401
    session.clear()
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify({"user": db.get_user_by_id(user["id"]), "csrf_token": session["csrf_token"]}), 200


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True}), 200


@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    denied = role_required("admin")
    if denied:
        return denied
    return jsonify(db.list_users()), 200


@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
def update_admin_user(user_id):
    denied = role_required("admin")
    if denied:
        return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON user update is required."}), 400
    role = data.get("role")
    department = str(data.get("department", "")).strip()
    is_active = data.get("is_active", True)
    if role not in ("admin", "manager", "user") or not isinstance(is_active, bool):
        return jsonify({"error": "A valid role and active status are required."}), 400
    if role == "manager" and not department:
        return jsonify({"error": "Department managers must be assigned a department."}), 400
    if role != "manager":
        department = ""
    target = db.get_user_by_id(user_id)
    if target is None:
        return jsonify({"error": "User not found."}), 404
    if target["role"] == "admin" and (role != "admin" or not is_active):
        active_admins = [user for user in db.list_users() if user["role"] == "admin" and user["is_active"]]
        if len(active_admins) <= 1:
            return jsonify({"error": "The final active administrator cannot be demoted or disabled."}), 409
    db.update_user(user_id, role, department, is_active)
    db.add_admin_audit_log(
        g.current_user["id"], "update_user", "user", user_id,
        f"Role={role}; department={department or 'none'}; active={is_active}",
    )
    logger.info("Administrator %s updated account %s", g.current_user["id"], user_id)
    return jsonify(db.get_user_by_id(user_id)), 200

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
ALLOWED_PERIODS = ["daily", "weekly", "monthly"]


# ── REST API: Resource entries ─────────────────────────────────────────
@app.route("/api/entries", methods=["GET"])
def list_entries():
    """Return records visible to the signed-in user's role."""
    try:
        user = g.current_user
        entries = db.get_all_entries(
            owner_user_id=user["id"] if user["role"] == "user" else None,
            department_names=assigned_departments(user) if user["role"] == "manager" else None,
        )
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
    denied = role_required("admin", "manager", "user")
    if denied:
        return denied

    resource = str(data.get("resource", "")).strip()
    area = str(data.get("area", "")).strip()
    unit = str(data.get("unit", "")).strip()
    date_str = str(data.get("date", "")).strip()
    reporter = str(data.get("reporter", "")).strip()[:80]
    note = str(data.get("note", "")).strip()[:200]

    if not resource or not area or not unit or not date_str:
        return jsonify({"error": "Fields 'resource', 'amount', 'unit', 'area', and 'date' are required."}), 400

    try:
        if date.fromisoformat(date_str).isoformat() != date_str:
            raise ValueError("Date must use YYYY-MM-DD format.")
    except ValueError:
        return jsonify({"error": "Date must be a valid YYYY-MM-DD calendar date."}), 400

    if resource not in ALLOWED_RESOURCES:
        return jsonify({"error": f"Invalid resource type: '{resource}'. Allowed: {', '.join(ALLOWED_RESOURCES)}"}), 400
    unit = unit[:20]
    if db.resource_unit_factor(resource, unit) is None:
        return jsonify({"error": f"Unit '{unit}' is not supported for resource '{resource}'."}), 400

    raw_amount = data.get("amount")
    try:
        amount = float(raw_amount)
    except (ValueError, TypeError):
        return jsonify({"error": "Amount must be a valid numeric value."}), 400

    if math.isnan(amount) or math.isinf(amount) or amount < 0:
        return jsonify({"error": "Amount must be a non-negative finite number."}), 400

    if amount > 10_000_000:
        return jsonify({"error": "Amount exceeds maximum allowable threshold (10,000,000)."}), 400

    user = g.current_user
    department = str(data.get("department") or "").strip()[:100]
    if user["role"] in ("manager", "user"):
        department = user["department"] or "Unassigned"
    payload = {
        "resource": resource,
        "amount": round(amount, 3),
        "unit": unit,
        "area": area[:100],
        "building": str(data.get("building") or "").strip()[:100] or area[:100],
        "department": department or "Unassigned",
        "date": date_str,
        "reporter": user["display_name"] if user["role"] != "admin" else reporter or user["display_name"],
        "note": note,
        "is_demo": False,
        "owner_user_id": user["id"],
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
        user = g.current_user
        entry = db.get_entry(entry_id)
        if entry is None:
            return jsonify({"error": f"Entry with ID {entry_id} not found."}), 404
        allowed = (
            user["role"] == "admin"
            or (user["role"] == "manager" and entry["department"] == user["department"])
            or (entry["owner_user_id"] == user["id"])
        )
        if not allowed:
            return jsonify({"error": "You do not have permission to delete this entry."}), 403
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
        user = g.current_user
        if user["role"] == "admin":
            return jsonify(db.get_totals()), 200
        entries = db.get_all_entries(
            owner_user_id=user["id"] if user["role"] == "user" else None,
            department_names=assigned_departments(user) if user["role"] == "manager" else None,
        )
        totals_by_resource = {resource: 0.0 for resource in ALLOWED_RESOURCES}
        for entry in entries:
            totals_by_resource[entry["resource"]] += float(entry["amount"])
        return jsonify({key: round(value, 2) for key, value in totals_by_resource.items()}), 200
    except Exception as exc:
        logger.error(f"Error computing totals: {exc}")
        return jsonify({"error": "Failed to compute totals."}), 500


def monitoring_configuration():
    """Read validated monitoring defaults from environment configuration."""
    try:
        limit_multiplier = float(os.getenv("MONITORING_LIMIT_MULTIPLIER", "1.5"))
        under_usage_ratio = float(os.getenv("MONITORING_UNDER_USAGE_RATIO", "0.5"))
        baseline_limits = json.loads(os.getenv("MONITORING_BASELINE_LIMITS", "{}"))
        if not math.isfinite(limit_multiplier) or not 0 < limit_multiplier <= 10:
            raise ValueError("MONITORING_LIMIT_MULTIPLIER must be between 0 and 10.")
        if not math.isfinite(under_usage_ratio) or not 0 <= under_usage_ratio <= 1:
            raise ValueError("MONITORING_UNDER_USAGE_RATIO must be between 0 and 1.")
        if not isinstance(baseline_limits, dict):
            raise ValueError("MONITORING_BASELINE_LIMITS must be a JSON object.")
        for key, value in baseline_limits.items():
            if key not in ALLOWED_RESOURCES or isinstance(value, bool):
                raise ValueError("MONITORING_BASELINE_LIMITS must map resource names to positive numbers.")
            numeric_value = float(value)
            if not math.isfinite(numeric_value) or numeric_value <= 0:
                raise ValueError("MONITORING_BASELINE_LIMITS must map resource names to positive numbers.")
            baseline_limits[key] = numeric_value
        return limit_multiplier, under_usage_ratio, baseline_limits
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid resource monitoring configuration: {exc}") from exc


@app.route("/api/monitoring", methods=["GET"])
def resource_monitoring():
    period = request.args.get("period", "daily")
    resource = request.args.get("resource", "electricity")
    if period not in ALLOWED_PERIODS:
        return jsonify({"error": f"Invalid period. Must be one of: {', '.join(ALLOWED_PERIODS)}."}), 400
    if resource != "all" and resource not in ALLOWED_RESOURCES:
        return jsonify({"error": f"Invalid resource. Must be one of: {', '.join(ALLOWED_RESOURCES)}."}), 400
    try:
        multiplier, under_ratio, baselines = monitoring_configuration()
        result = db.get_resource_monitoring(
            period, resource, multiplier, under_ratio, baselines,
            department_names=(
                assigned_departments(g.current_user)
                if g.current_user["role"] == "manager" else None
            ),
        )
        if g.current_user["role"] == "user":
            result["buildings"] = []
            result["departments"] = []
            result["alerts"] = []
            summary = result.get("university")
            if summary:
                summary.pop("highest_consuming_building", None)
                summary.pop("lowest_consuming_building", None)
                summary.pop("buildings_over_limit", None)
                summary.pop("buildings_within_limit", None)
                summary.pop("buildings_under_usage", None)
        return jsonify(result), 200
    except ValueError as exc:
        logger.error("%s", exc)
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        logger.error("Error computing resource monitoring: %s", exc)
        return jsonify({"error": "Failed to compute resource monitoring."}), 500


@app.route("/api/monitoring/limits", methods=["GET", "POST"])
def consumption_limits():
    denied = role_required("admin")
    if denied:
        return denied
    if request.method == "GET":
        try:
            return jsonify({
                "limits": db.get_consumption_limits(),
                "admin_updates_enabled": True,
            }), 200
        except Exception as exc:
            logger.error("Error fetching consumption limits: %s", exc)
            return jsonify({"error": "Failed to fetch consumption limits."}), 500

    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON request payload."}), 400

    scope_type = data.get("scope_type")
    scope_name = str(data.get("scope_name", "")).strip()
    resource = data.get("resource")
    period = data.get("period")
    if scope_type not in ("building", "department", "university"):
        return jsonify({"error": "scope_type must be building, department, or university."}), 400
    if scope_type == "university":
        scope_name = "University"
    elif not scope_name or len(scope_name) > 100:
        return jsonify({"error": "scope_name must contain 1 to 100 characters."}), 400
    if resource not in ALLOWED_RESOURCES:
        return jsonify({"error": f"resource must be one of: {', '.join(ALLOWED_RESOURCES)}."}), 400
    if period not in ALLOWED_PERIODS:
        return jsonify({"error": f"period must be one of: {', '.join(ALLOWED_PERIODS)}."}), 400

    raw_limit = data.get("max_consumption")
    try:
        maximum = float(raw_limit)
    except (TypeError, ValueError):
        return jsonify({"error": "max_consumption must be a positive finite number."}), 400
    if isinstance(raw_limit, bool) or not math.isfinite(maximum) or not 0 < maximum <= 1_000_000_000_000:
        return jsonify({"error": "max_consumption must be a positive finite number no greater than 1,000,000,000,000."}), 400

    try:
        _, default_under_ratio, _ = monitoring_configuration()
    except ValueError as exc:
        logger.error("%s", exc)
        return jsonify({"error": str(exc)}), 500
    raw_under_ratio = data.get("under_usage_ratio", default_under_ratio)
    try:
        under_ratio = float(raw_under_ratio)
    except (TypeError, ValueError):
        return jsonify({"error": "under_usage_ratio must be a number between 0 and 1."}), 400
    if isinstance(raw_under_ratio, bool) or not math.isfinite(under_ratio) or not 0 <= under_ratio <= 1:
        return jsonify({"error": "under_usage_ratio must be a number between 0 and 1."}), 400

    try:
        db.set_consumption_limit(scope_type, scope_name, resource, period, maximum, under_ratio)
        db.add_admin_audit_log(
            g.current_user["id"], "update_limit", "consumption_limit",
            f"{scope_type}:{scope_name}:{resource}:{period}",
            f"Maximum={maximum}; under-usage ratio={under_ratio}",
        )
        return jsonify({"ok": True, "message": "Consumption limit saved."}), 200
    except Exception as exc:
        logger.error("Error saving consumption limit: %s", exc)
        return jsonify({"error": "Failed to save consumption limit."}), 500


# ── REST API: Green actions ────────────────────────────────────────────
@app.route("/api/actions", methods=["GET"])
def list_actions():
    """Return the signed-in user's completed green action indices."""
    try:
        return jsonify(db.get_completed_actions(g.current_user["id"])), 200
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
        db.set_action(idx, completed, g.current_user["id"])
        return jsonify({"ok": True, "completed_actions": db.get_completed_actions(g.current_user["id"])}), 200
    except Exception as exc:
        logger.error(f"Error updating action {idx}: {exc}")
        return jsonify({"error": "Failed to update action."}), 500


# ── REST API: Incident Issue Tickets ──────────────────────────────────
@app.route("/api/issues", methods=["GET"])
def list_issues():
    """List incidents visible to the signed-in user's role."""
    try:
        user = g.current_user
        return jsonify(db.get_all_issues(
            owner_user_id=user["id"] if user["role"] == "user" else None,
            department_names=assigned_departments(user) if user["role"] == "manager" else None,
        )), 200
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
    description = str(data.get("description", "")).strip()

    if not title or not area:
        return jsonify({"error": "Both 'title' and 'area' are required non-empty fields."}), 400

    if category not in ALLOWED_CATEGORIES:
        category = "other"

    if priority not in ALLOWED_PRIORITIES:
        priority = "Medium"

    user = g.current_user
    department = str(data.get("department") or "").strip()[:100]
    if user["role"] in ("user", "manager"):
        department = user["department"]
    payload = {
        "title": title[:120],
        "area": area[:100],
        "category": category,
        "priority": priority,
        "reporter": user["display_name"],
        "description": description[:300],
        "department": department,
        "owner_user_id": user["id"],
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
    user = g.current_user
    denied = role_required("admin", "manager")
    if denied:
        return denied
    issue = db.get_issue(issue_id)
    if issue is None:
        return jsonify({"error": f"Issue with ID {issue_id} not found."}), 404
    if user["role"] == "manager" and issue["department"] != user["department"]:
        return jsonify({"error": "You do not have permission to update this incident."}), 403
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON request payload."}), 400

    status = data.get("status")
    if status not in ALLOWED_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(ALLOWED_STATUSES)}."}), 400

    try:
        if db.update_issue_status(issue_id, status):
            db.add_admin_audit_log(user["id"], "update_incident", "incident", issue_id, f"Status={status}")
            return jsonify({"ok": True, "message": f"Issue status updated to {status}."}), 200
        return jsonify({"error": f"Issue with ID {issue_id} not found."}), 404
    except Exception as exc:
        logger.error(f"Error updating issue status: {exc}")
        return jsonify({"error": "Failed to update status."}), 500


@app.route("/api/issues/<int:issue_id>", methods=["DELETE"])
def remove_issue(issue_id):
    """Delete an issue ticket."""
    denied = role_required("admin")
    if denied:
        return denied
    try:
        if db.delete_issue(issue_id):
            db.add_admin_audit_log(g.current_user["id"], "delete_incident", "incident", issue_id)
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
        departments = (
            assigned_departments(g.current_user)
            if g.current_user["role"] == "manager" else None
        )
        return jsonify(db.get_leaderboard(departments)), 200
    except Exception as exc:
        logger.error(f"Error generating leaderboard: {exc}")
        return jsonify({"error": "Failed to generate leaderboard."}), 500


@app.route("/api/insights", methods=["GET"])
def get_insights():
    """Return AI Eco-Advisor recommendations, spike detection, and savings."""
    try:
        user = g.current_user
        return jsonify(db.get_insights(
            department_names=assigned_departments(user) if user["role"] == "manager" else None,
            owner_user_id=user["id"] if user["role"] == "user" else None,
        )), 200
    except Exception as exc:
        logger.error(f"Error computing insights: {exc}")
        return jsonify({"error": "Failed to compute insights."}), 500


# ── REST API: IoT Telemetry Webhook Endpoint ──────────────────────────
@app.route("/api/sensors/ingest", methods=["POST"])
def sensor_ingest():
    """
    Ingest telemetry from the signed-in administrator's simulator.
    Validates payload against corrupt values, invalid resources, and overflows.
    """
    user = g.current_user
    denied = role_required("admin")
    if denied:
        return denied

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

    if math.isnan(amount) or math.isinf(amount) or amount < 0:
        return jsonify({"error": "Telemetry 'amount' must be a non-negative finite number."}), 400

    if amount > 10_000_000:
        return jsonify({"error": "Telemetry 'amount' exceeds maximum allowable threshold."}), 400

    default_units = {"electricity": "kWh", "water": "L", "paper": "sheets", "waste": "kg", "generalWaste": "kg"}
    unit = str(data.get("unit", default_units[resource])).strip()[:20]
    if db.resource_unit_factor(resource, unit) is None:
        return jsonify({"error": f"Unit '{unit}' is not supported for resource '{resource}'."}), 400
    area = str(data.get("area", "Smart Meter Zone")).strip()[:100]
    sensor_id = str(data.get("sensor_id", "IoT-Sensor")).strip()[:50]

    entry_payload = {
        "resource": resource,
        "amount": round(amount, 3),
        "unit": unit,
        "area": area,
        "building": str(data.get("building") or "").strip()[:100] or area,
        "department": str(data.get("department") or "").strip()[:100] or "Unassigned",
        "date": date.today().isoformat(),
        "reporter": f"IoT Meter ({sensor_id})",
        "note": f"Automated reading received via /api/sensors/ingest",
        "is_demo": False,
        "owner_user_id": user["id"] if user else None,
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
    denied = role_required("admin")
    if denied:
        return denied
    try:
        entries = db.get_all_entries()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Date", "Resource", "Quantity", "Unit", "Area", "Building", "Department", "Logged by", "Note", "Sample data"])
        for e in entries:
            writer.writerow([
                sanitize_csv_cell(e["date"]),
                sanitize_csv_cell(LABELS.get(e["resource"], e["resource"])),
                e["amount"],
                sanitize_csv_cell(e["unit"]),
                sanitize_csv_cell(e["area"]),
                sanitize_csv_cell(e.get("building", e["area"])),
                sanitize_csv_cell(e.get("department", "Unassigned")),
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


@app.route("/api/admin/audit-logs", methods=["GET"])
def admin_audit_logs():
    denied = role_required("admin")
    if denied:
        return denied
    return jsonify(db.get_admin_audit_logs()), 200


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
