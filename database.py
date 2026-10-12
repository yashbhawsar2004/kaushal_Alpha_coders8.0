"""
database.py — SQLite database layer for EcoCampus.
Handles table creation, CRUD operations, transactions, data integrity,
incident issue tickets, departmental leaderboard scoring, and AI insights.
"""

import os
import sqlite3
import math
import secrets
from datetime import date, datetime, timezone
from werkzeug.security import generate_password_hash

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecocampus.db")
DB_PATH = os.getenv("DATABASE_PATH", DEFAULT_DB)
RESOURCE_CANONICAL_UNITS = {
    "electricity": "kWh",
    "water": "L",
    "paper": "sheets",
    "waste": "kg",
    "generalWaste": "kg",
}
RESOURCE_UNIT_FACTORS = {
    "electricity": {"kwh": 1.0, "wh": 0.001, "mwh": 1000.0},
    "water": {
        "l": 1.0, "liter": 1.0, "litre": 1.0, "liters": 1.0, "litres": 1.0,
        "ml": 0.001, "kl": 1000.0, "kiloliter": 1000.0, "kilolitre": 1000.0,
        "kiloliters": 1000.0, "kilolitres": 1000.0, "m3": 1000.0, "m³": 1000.0,
    },
    "paper": {"sheet": 1.0, "sheets": 1.0, "ream": 500.0, "reams": 500.0},
    "waste": {"kg": 1.0, "kilogram": 1.0, "kilograms": 1.0, "g": 0.001, "gram": 0.001,
              "grams": 0.001, "t": 1000.0, "tonne": 1000.0, "tonnes": 1000.0},
    "generalWaste": {"kg": 1.0, "kilogram": 1.0, "kilograms": 1.0, "g": 0.001, "gram": 0.001,
                     "grams": 0.001, "t": 1000.0, "tonne": 1000.0, "tonnes": 1000.0},
}

# Ensure parent directory exists if a custom path is supplied
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_connection():
    """Return a new connection with row_factory set to sqlite3.Row and concurrency timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def get_utc_now_iso():
    """Return current UTC timestamp in ISO 8601 format (Python 3.12+ safe)."""
    return datetime.now(timezone.utc).isoformat()


def resource_unit_factor(resource, unit):
    """Return conversion to the canonical resource unit, or None for unsupported units."""
    normalized = str(unit).strip().lower().replace(" ", "")
    return RESOURCE_UNIT_FACTORS.get(resource, {}).get(normalized)


def init_db():
    """Create tables and indexes if they don't exist and seed initial demo data."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                display_name    TEXT    NOT NULL,
                password_hash   TEXT    NOT NULL,
                role            TEXT    NOT NULL DEFAULT 'user'
                                CHECK(role IN ('admin', 'manager', 'user')),
                department      TEXT    NOT NULL DEFAULT '',
                is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action      TEXT    NOT NULL,
                target_type TEXT    NOT NULL,
                target_id   TEXT    NOT NULL,
                details     TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key   TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)

        # 1. Resource entries
        cur.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                resource    TEXT    NOT NULL,
                amount      REAL    NOT NULL CHECK(amount >= 0),
                unit        TEXT    NOT NULL,
                area        TEXT    NOT NULL,
                building    TEXT    NOT NULL DEFAULT '',
                department  TEXT    NOT NULL DEFAULT 'Unassigned',
                date        TEXT    NOT NULL,
                reporter    TEXT    DEFAULT '',
                note        TEXT    DEFAULT '',
                is_demo     INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        entry_columns = {row["name"] for row in cur.execute("PRAGMA table_info(entries)")}
        if "building" not in entry_columns:
            cur.execute("ALTER TABLE entries ADD COLUMN building TEXT NOT NULL DEFAULT ''")
        if "department" not in entry_columns:
            cur.execute("ALTER TABLE entries ADD COLUMN department TEXT NOT NULL DEFAULT 'Unassigned'")
        if "owner_user_id" not in entry_columns:
            cur.execute("ALTER TABLE entries ADD COLUMN owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        cur.execute("UPDATE entries SET building = area WHERE building = ''")
        table_sql = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
        ).fetchone()["sql"]
        if "CHECK(amount>0)" in table_sql.replace(" ", ""):
            cur.execute("ALTER TABLE entries RENAME TO entries_positive_only")
            cur.execute("""
                CREATE TABLE entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource    TEXT    NOT NULL,
                    amount      REAL    NOT NULL CHECK(amount >= 0),
                    unit        TEXT    NOT NULL,
                    area        TEXT    NOT NULL,
                    building    TEXT    NOT NULL DEFAULT '',
                    department  TEXT    NOT NULL DEFAULT 'Unassigned',
                    date        TEXT    NOT NULL,
                    reporter    TEXT    DEFAULT '',
                    note        TEXT    DEFAULT '',
                    is_demo     INTEGER DEFAULT 0,
                    created_at  TEXT    DEFAULT (datetime('now')),
                    owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
                )
            """)
            cur.execute("""
                INSERT INTO entries
                    (id, resource, amount, unit, area, building, department, date,
                     reporter, note, is_demo, created_at, owner_user_id)
                SELECT id, resource, amount, unit, area, building, department, date,
                       reporter, note, is_demo, created_at, owner_user_id
                FROM entries_positive_only
            """)
            cur.execute("DROP TABLE entries_positive_only")

        # 2. Checklist actions (unique action index constraint)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                action_idx  INTEGER NOT NULL UNIQUE,
                completed   INTEGER DEFAULT 1,
                updated_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_actions (
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                action_idx  INTEGER NOT NULL CHECK(action_idx BETWEEN 0 AND 4),
                completed   INTEGER NOT NULL DEFAULT 1 CHECK(completed IN (0, 1)),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, action_idx)
            )
        """)

        # 3. Incident reporting & maintenance tickets
        cur.execute("""
            CREATE TABLE IF NOT EXISTS issues (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                category    TEXT    NOT NULL,
                area        TEXT    NOT NULL,
                description TEXT    DEFAULT '',
                priority    TEXT    DEFAULT 'Medium',
                status      TEXT    DEFAULT 'Reported',
                reporter    TEXT    DEFAULT 'Student Volunteer',
                created_at  TEXT    DEFAULT (datetime('now')),
                resolved_at TEXT
            )
        """)
        issue_columns = {row["name"] for row in cur.execute("PRAGMA table_info(issues)")}
        if "owner_user_id" not in issue_columns:
            cur.execute("ALTER TABLE issues ADD COLUMN owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        if "department" not in issue_columns:
            cur.execute("ALTER TABLE issues ADD COLUMN department TEXT NOT NULL DEFAULT ''")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS consumption_limits (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type          TEXT    NOT NULL CHECK(scope_type IN ('building', 'department', 'university')),
                scope_name          TEXT    NOT NULL,
                resource            TEXT    NOT NULL,
                period              TEXT    NOT NULL CHECK(period IN ('daily', 'weekly', 'monthly')),
                max_consumption     REAL    NOT NULL CHECK(max_consumption > 0),
                under_usage_ratio   REAL    NOT NULL DEFAULT 0.5 CHECK(under_usage_ratio >= 0 AND under_usage_ratio <= 1),
                updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(scope_type, scope_name, resource, period)
            )
        """)

        # Indexes for query optimization
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_resource ON entries(resource)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_owner ON entries(owner_user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_issues_owner ON issues(owner_user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_issues_department ON issues(department)")

        # Seed demo entries if table is completely empty
        cur.execute("SELECT COUNT(*) FROM entries")
        if cur.fetchone()[0] == 0:
            today = date.today().isoformat()
            demo_rows = [
                ("electricity", 42, "kWh", "Computer Lab 2", "Tech Tower", "Computer Science & IT", today, "EcoClub Team", "Evening lab monitoring", 1),
                ("water", 280, "L", "Washroom Block A", "Central Block", "Facilities", today, "Campus Ops", "Metered flush cycle", 1),
                ("paper", 80, "sheets", "Admin Office", "Central Block", "Management & Arts", today, "Staff Council", "Exam sheets printed", 1),
                ("waste", 6, "kg", "Main Canteen", "Central Block", "Management & Arts", today, "Green Ambassadors", "Food compost segregated", 1),
                ("electricity", 35, "kWh", "Lecture Hall A", "Science Complex", "Electronics & Comm.", today, "EcoClub Team", "Projector & AC usage", 1),
                ("water", 310, "L", "Botanical Garden", "Green Block", "Civil & Environmental", today, "Groundskeeping", "Drip irrigation run", 1),
                ("electricity", 65, "kWh", "Mechanical Workshop", "West Wing", "Mechanical Engineering", today, "Faculty rep", "Machinery testing", 1),
                ("waste", 12, "kg", "Library Quad", "Tech Tower", "Computer Science & IT", today, "EcoClub Team", "Plastic recycling bin audit", 1),
            ]
            cur.executemany(
                "INSERT INTO entries (resource, amount, unit, area, building, department, date, reporter, note, is_demo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                demo_rows,
            )

        # Seed demo issues if table is empty
        cur.execute("SELECT COUNT(*) FROM issues")
        if cur.fetchone()[0] == 0:
            demo_issues = [
                ("Dripping Tap in 2nd Floor Washroom", "water", "Washroom Block A", "Tap valve leaking continuously at sink #3.", "High", "In Progress", "Aarav Sharma"),
                ("AC Running in Empty Seminar Hall", "electricity", "Seminar Hall B", "Air conditioning left at 18C after 5 PM session.", "Medium", "Reported", "Priya Verma"),
                ("Recycling Bin Overflowing", "waste", "Canteen Courtyard", "Paper and dry recycling bin needs clearing.", "Low", "Resolved", "Green Ambassador Team"),
            ]
            cur.executemany(
                "INSERT INTO issues (title, category, area, description, priority, status, reporter) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                demo_issues,
            )

        conn.commit()
    finally:
        conn.close()


def add_admin_audit_log(actor_id, action, target_type, target_id, details=""):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO admin_audit_logs (actor_id, action, target_type, target_id, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (actor_id, action, target_type, str(target_id), details[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def get_admin_audit_logs(limit=200):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT l.id, l.actor_id, u.username AS actor_username, l.action, l.target_type, "
            "l.target_id, l.details, l.created_at FROM admin_audit_logs l "
            "LEFT JOIN users u ON u.id = l.actor_id ORDER BY l.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_or_create_session_secret():
    conn = get_connection()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_settings "
            "(setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)"
        )
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = 'session_secret'"
        ).fetchone()
        if row:
            return row["setting_value"]
        secret = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES ('session_secret', ?)",
            (secret,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = 'session_secret'"
        ).fetchone()
        return row["setting_value"]
    finally:
        conn.close()


def get_issue(issue_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Entry CRUD ─────────────────────────────────────────────────────────

def get_all_entries(owner_user_id=None, department_names=None):
    conn = get_connection()
    try:
        query = "SELECT * FROM entries"
        clauses = []
        values = []
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            values.append(owner_user_id)
        if department_names is not None:
            names = list(department_names)
            if not names:
                return []
            clauses.append("department IN (" + ",".join("?" for _ in names) + ")")
            values.extend(names)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(query + " ORDER BY date DESC, id DESC", values).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_entry(data):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO entries (resource, amount, unit, area, building, department, date, reporter, note, is_demo, owner_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["resource"],
                float(data["amount"]),
                data["unit"],
                data["area"],
                data.get("building") or data["area"],
                data.get("department") or "Unassigned",
                data["date"],
                data.get("reporter", ""),
                data.get("note", ""),
                1 if data.get("is_demo") else 0,
                data.get("owner_user_id"),
            ),
        )
        entry_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_entry(entry_id):
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_entry(entry_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_username(username):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, display_name, role, department, is_active, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_user(username, display_name, password, role="user", department=""):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role, department) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, display_name, generate_password_hash(password), role, department),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def ensure_bootstrap_admin(username, password):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        password_hash = generate_password_hash(password)
        if existing:
            conn.execute(
                "UPDATE users SET display_name = ?, password_hash = ?, role = 'admin', "
                "department = '', is_active = 1 WHERE id = ?",
                (username, password_hash, existing["id"]),
            )
            user_id = existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO users (username, display_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (username, username, password_hash),
            )
            user_id = cursor.lastrowid
        conn.commit()
        return user_id
    finally:
        conn.close()


def list_users():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, display_name, role, department, is_active, created_at "
            "FROM users ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_user(user_id, role, department, is_active):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE users SET role = ?, department = ?, is_active = ? WHERE id = ?",
            (role, department, int(is_active), user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ── Action helpers ─────────────────────────────────────────────────────

def get_completed_actions(user_id=None):
    conn = get_connection()
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT action_idx FROM user_actions WHERE user_id = ? AND completed = 1 ORDER BY action_idx ASC",
                (user_id,),
            ).fetchall()
            return [r["action_idx"] for r in rows]
        rows = conn.execute("SELECT action_idx FROM actions WHERE completed = 1 ORDER BY action_idx ASC").fetchall()
        return [r["action_idx"] for r in rows]
    finally:
        conn.close()


def set_action(action_idx, completed, user_id=None):
    conn = get_connection()
    try:
        if user_id is not None:
            if completed:
                conn.execute(
                    "INSERT INTO user_actions (user_id, action_idx, completed, updated_at) "
                    "VALUES (?, ?, 1, ?) ON CONFLICT(user_id, action_idx) "
                    "DO UPDATE SET completed = 1, updated_at = excluded.updated_at",
                    (user_id, action_idx, get_utc_now_iso()),
                )
            else:
                conn.execute(
                    "DELETE FROM user_actions WHERE user_id = ? AND action_idx = ?",
                    (user_id, action_idx),
                )
            conn.commit()
            return
        if completed:
            conn.execute(
                "INSERT INTO actions (action_idx, completed, updated_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(action_idx) DO UPDATE SET completed=1, updated_at=?",
                (action_idx, get_utc_now_iso(), get_utc_now_iso()),
            )
        else:
            conn.execute("DELETE FROM actions WHERE action_idx = ?", (action_idx,))
        conn.commit()
    finally:
        conn.close()


def get_totals():
    """Return aggregated totals per resource type."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT resource, COALESCE(SUM(amount), 0) as total FROM entries GROUP BY resource"
        ).fetchall()
        totals_map = {r["resource"]: round(float(r["total"]), 2) for r in rows}
        # Guarantee all core categories exist in response
        for k in ["electricity", "water", "paper", "waste", "generalWaste"]:
            totals_map.setdefault(k, 0.0)
        return totals_map
    finally:
        conn.close()


def set_consumption_limit(scope_type, scope_name, resource, period, max_consumption, under_usage_ratio):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO consumption_limits
                (scope_type, scope_name, resource, period, max_consumption, under_usage_ratio, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_name, resource, period) DO UPDATE SET
                max_consumption = excluded.max_consumption,
                under_usage_ratio = excluded.under_usage_ratio,
                updated_at = excluded.updated_at
            """,
            (
                scope_type,
                scope_name,
                resource,
                period,
                float(max_consumption),
                float(under_usage_ratio),
                get_utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_consumption_limits():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT scope_type, scope_name, resource, period, max_consumption, "
            "under_usage_ratio, updated_at FROM consumption_limits "
            "ORDER BY scope_type, scope_name, resource, period"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_resource_monitoring(period, resource, limit_multiplier=1.5, default_under_usage_ratio=0.5,
                           baseline_limits=None, department_names=None):
    """Aggregate resource readings into comparable periods and scope-specific statuses."""
    period_formats = {
        "daily": lambda value: value.isoformat(),
        "weekly": lambda value: f"{value.isocalendar().year}-W{value.isocalendar().week:02d}",
        "monthly": lambda value: value.strftime("%Y-%m"),
    }
    max_stale_days = {"daily": 2, "weekly": 14, "monthly": 62}[period]
    baseline_limits = baseline_limits or {}

    conn = get_connection()
    try:
        if department_names is None:
            rows = conn.execute(
                "SELECT resource, amount, unit, area, building, department, date, is_demo FROM entries"
            ).fetchall()
            limit_rows = conn.execute(
                "SELECT scope_type, scope_name, resource, period, max_consumption, under_usage_ratio "
                "FROM consumption_limits WHERE period = ?",
                (period,),
            ).fetchall()
        else:
            names = list(department_names)
            if names:
                placeholders = ",".join("?" for _ in names)
                rows = conn.execute(
                    "SELECT resource, amount, unit, area, building, department, date, is_demo "
                    f"FROM entries WHERE department IN ({placeholders})",
                    names,
                ).fetchall()
                limit_rows = conn.execute(
                    "SELECT scope_type, scope_name, resource, period, max_consumption, under_usage_ratio "
                    f"FROM consumption_limits WHERE period = ? AND "
                    f"(scope_type = 'department' AND scope_name IN ({placeholders}))",
                    [period, *names],
                ).fetchall()
            else:
                rows = []
                limit_rows = []
    finally:
        conn.close()

    limits = {
        (row["scope_type"], row["scope_name"], row["resource"]): row
        for row in limit_rows
    }
    by_resource = {}
    invalid_date_entries = 0
    invalid_unit_entries = 0
    invalid_dates_by_resource = {}
    invalid_units_by_resource = {}
    for row in rows:
        try:
            reading_date = date.fromisoformat(row["date"])
        except (TypeError, ValueError):
            invalid_date_entries += 1
            invalid_dates_by_resource[row["resource"]] = (
                invalid_dates_by_resource.get(row["resource"], 0) + 1
            )
            continue
        unit_factor = resource_unit_factor(row["resource"], row["unit"])
        if unit_factor is None:
            invalid_unit_entries += 1
            invalid_units_by_resource[row["resource"]] = (
                invalid_units_by_resource.get(row["resource"], 0) + 1
            )
            continue
        resource_rows = by_resource.setdefault(row["resource"], [])
        resource_rows.append({
            "date": reading_date,
            "period_key": period_formats[period](reading_date),
            "amount": float(row["amount"]) * unit_factor,
            "unit": RESOURCE_CANONICAL_UNITS[row["resource"]],
            "building": row["building"] or row["area"],
            "department": row["department"] or "Unassigned",
            "is_demo": bool(row["is_demo"]),
        })

    resources = sorted(
        set(by_resource) | set(baseline_limits) | set(RESOURCE_CANONICAL_UNITS)
    )
    results = {}
    alerts = []
    seen_alerts = set()

    for resource_name in resources:
        readings = by_resource.get(resource_name, [])
        period_keys = sorted({row["period_key"] for row in readings})
        latest_key = period_keys[-1] if period_keys else None
        as_of_date = max((row["date"] for row in readings), default=None)
        unit = RESOURCE_CANONICAL_UNITS.get(resource_name, readings[-1]["unit"] if readings else "")
        building_periods = {}
        department_periods = {}
        building_departments = {}
        building_department_dates = {}
        entity_last_dates = {}

        for reading in readings:
            bucket = reading["period_key"]
            building = reading["building"]
            department = reading["department"]
            building_periods.setdefault(building, {}).setdefault(bucket, 0.0)
            building_periods[building][bucket] += reading["amount"]
            department_periods.setdefault(department, {}).setdefault(bucket, 0.0)
            department_periods[department][bucket] += reading["amount"]
            if reading["date"] >= building_department_dates.get(building, reading["date"]):
                building_departments[building] = department
                building_department_dates[building] = reading["date"]
            entity_last_dates[("building", building)] = max(
                reading["date"], entity_last_dates.get(("building", building), reading["date"])
            )
            entity_last_dates[("department", department)] = max(
                reading["date"], entity_last_dates.get(("department", department), reading["date"])
            )

        trend_keys = period_keys[-12:]
        resource_readings = readings

        def make_metric(scope_type, scope_name, actual, average, observations, trend, last_date,
                        samples, active_in_latest=True):
            limit_row = limits.get((scope_type, scope_name, resource_name))
            fallback = baseline_limits.get(resource_name)
            if limit_row:
                maximum = float(limit_row["max_consumption"])
                under_ratio = float(limit_row["under_usage_ratio"])
                limit_source = "Administrator configured"
            elif average is not None and average > 0:
                maximum = average * limit_multiplier
                under_ratio = default_under_usage_ratio
                limit_source = (
                    "Observed-period sample baseline × configured limit multiplier"
                    if observations < 2 else
                    "Historical baseline × configured limit multiplier"
                )
            elif fallback is not None:
                maximum = float(fallback)
                under_ratio = default_under_usage_ratio
                limit_source = "Configured sample-data baseline"
            else:
                maximum = None
                under_ratio = default_under_usage_ratio
                limit_source = "No limit or baseline configured"

            if not active_in_latest:
                status = "Missing Data"
                quality = "No reading in the latest campus period"
            elif maximum is None:
                status = "Missing Data"
                quality = "Insufficient history and no configured baseline or limit"
            elif actual > maximum:
                status = "Over Consumption"
                quality = "Measured reading compared with configured limit"
            elif average is not None and average > 0 and actual < average * under_ratio:
                status = "Under Usage"
                quality = "Based on historical average"
            else:
                status = "Within Limit"
                quality = (
                    "Insufficient history: one observed period used as a baseline"
                    if observations < 2 else
                    "Historical data"
                )

            freshness_days = (date.today() - last_date).days if last_date else None
            if freshness_days is not None and freshness_days > max_stale_days:
                quality = f"Outdated: last reading was {freshness_days} days before the latest campus reading"

            return {
                "scope_type": scope_type,
                "name": scope_name,
                "actual": round(actual, 3),
                "total_consumption": round(actual, 3),
                "average": round(average, 3) if average is not None else None,
                "average_consumption": round(average, 3) if average is not None else None,
                "limit": round(maximum, 3) if maximum is not None else None,
                "configured_limit": round(maximum, 3) if maximum is not None else None,
                "limit_source": limit_source,
                "percent_limit": round(actual / maximum * 100, 1) if maximum else None,
                "difference": round(actual - average, 3) if average is not None else None,
                "status": status,
                "under_usage_ratio": under_ratio,
                "observations": observations,
                "last_reading_date": last_date.isoformat() if last_date else None,
                "sample_data": bool(samples) and all(samples),
                "data_quality": quality,
                "trend": trend,
            }

        def entity_metrics(scope_type, period_data, department_for_building=None):
            metrics = []
            for name, bucket_values in sorted(period_data.items()):
                values = [bucket_values[key] for key in sorted(bucket_values)]
                average = sum(values) / len(values) if values else None
                last_date = entity_last_dates.get((scope_type, name))
                samples = [
                    row["is_demo"] for row in resource_readings
                    if (row["building"] if scope_type == "building" else row["department"]) == name
                ]
                metrics.append(make_metric(
                    scope_type=scope_type,
                    scope_name=name,
                    actual=bucket_values.get(latest_key, 0.0) if latest_key else 0.0,
                    average=average,
                    observations=len(values),
                    trend=[
                        {"period": key, "value": round(bucket_values[key], 3), "has_data": True}
                        if key in bucket_values else {"period": key, "value": None, "has_data": False}
                        for key in trend_keys
                    ],
                    last_date=last_date,
                    samples=samples,
                    active_in_latest=bool(latest_key and latest_key in bucket_values),
                ))
                if department_for_building is not None:
                    metrics[-1]["department"] = department_for_building.get(name, "Unassigned")
            return metrics

        building_metrics = entity_metrics("building", building_periods, building_departments)
        department_metrics = entity_metrics("department", department_periods)
        campus_totals = {
            key: sum(values.get(key, 0.0) for values in building_periods.values())
            for key in period_keys
        }
        campus_average = sum(campus_totals.values()) / len(campus_totals) if campus_totals else None
        daily_totals = {}
        for reading in readings:
            daily_key = reading["date"].isoformat()
            daily_totals[daily_key] = daily_totals.get(daily_key, 0.0) + reading["amount"]
        campus_daily_average = (
            sum(daily_totals.values()) / len(daily_totals) if daily_totals else None
        )
        active_buildings = sum(
            1 for values in building_periods.values() if latest_key and latest_key in values
        )
        campus_samples = [row["is_demo"] for row in readings]
        university = make_metric(
            "university",
            "University",
            campus_totals.get(latest_key, 0.0) if latest_key else 0.0,
            campus_average,
            len(campus_totals),
            [
                {"period": key, "value": round(campus_totals[key], 3), "has_data": True}
                for key in trend_keys
            ],
            as_of_date,
            campus_samples,
            active_in_latest=bool(latest_key),
        )
        if university["limit_source"] != "Administrator configured":
            building_limit_total = sum(item["limit"] or 0.0 for item in building_metrics)
            building_limits_complete = bool(building_metrics) and all(
                item["limit"] is not None for item in building_metrics
            )
            if building_limits_complete and building_limit_total > 0:
                university["limit"] = round(building_limit_total, 3)
                university["configured_limit"] = round(building_limit_total, 3)
                university["limit_source"] = "Sum of building limits"
                university["percent_limit"] = round(university["actual"] / building_limit_total * 100, 1)
                if university["average"] is not None:
                    under_ratio = university["under_usage_ratio"]
                    if university["actual"] > building_limit_total:
                        university["status"] = "Over Consumption"
                    elif university["average"] > 0 and university["actual"] < university["average"] * under_ratio:
                        university["status"] = "Under Usage"
                    else:
                        university["status"] = "Within Limit"
            else:
                university["limit"] = None
                university["configured_limit"] = None
                university["limit_source"] = "Incomplete: building limits are missing"
                university["percent_limit"] = None
                university["status"] = "Missing Data"

        university["average_daily_consumption"] = (
            round(campus_daily_average, 3) if campus_daily_average is not None else None
        )
        university["average_daily_observations"] = len(daily_totals)
        university["average_per_building"] = (
            round(sum(item["average"] for item in building_metrics if item["average"] is not None)
                  / len([item for item in building_metrics if item["average"] is not None]), 3)
            if any(item["average"] is not None for item in building_metrics) else None
        )
        university["average_per_building_current_period"] = (
            round(university["actual"] / active_buildings, 3) if active_buildings else None
        )
        university["buildings_over_limit"] = sum(item["status"] == "Over Consumption" for item in building_metrics)
        university["buildings_within_limit"] = sum(item["status"] == "Within Limit" for item in building_metrics)
        university["buildings_under_usage"] = sum(item["status"] == "Under Usage" for item in building_metrics)
        university["total_configured_limit"] = university["limit"]
        measured_buildings = [item for item in building_metrics if item["status"] != "Missing Data"]
        university["highest_consuming_building"] = (
            max(measured_buildings, key=lambda item: item["actual"])["name"] if measured_buildings else None
        )
        university["lowest_consuming_building"] = (
            min(measured_buildings, key=lambda item: item["actual"])["name"] if measured_buildings else None
        )
        university["period"] = period
        university["period_key"] = latest_key
        university["unit"] = unit
        university["historical_periods"] = len(period_keys)
        university["data_quality"] = (
            "No valid readings available" if not readings else
            "Sample/demo readings; only one observed period" if all(campus_samples) and len(period_keys) < 2 else
            "Sample/demo readings" if all(campus_samples) else
            "Includes sample/demo readings" if any(campus_samples) else
            "Insufficient history: only one observed period" if len(period_keys) < 2 else
            "Historical readings"
        )
        if as_of_date and (date.today() - as_of_date).days > max_stale_days:
            university["data_quality"] += (
                f"; outdated: latest campus reading is {(date.today() - as_of_date).days} days old"
            )
        if university["limit_source"].startswith("Incomplete:"):
            university["data_quality"] += "; one or more building limits are not configured"
        resource_invalid_units = invalid_units_by_resource.get(resource_name, 0)
        if resource_invalid_units:
            university["data_quality"] += (
                f"; {resource_invalid_units} reading(s) excluded for unsupported units"
            )
        university["sample_data"] = bool(campus_samples) and all(campus_samples)

        for metric in [*building_metrics, *department_metrics, university]:
            metric["resource"] = resource_name
            metric["period"] = period
            metric["period_key"] = latest_key
            metric["unit"] = unit
            if resource != "all" and resource_name != resource:
                continue
            if metric["status"] == "Over Consumption":
                alert_key = (metric["scope_type"], metric["name"], resource_name, latest_key, metric["status"])
                if alert_key not in seen_alerts:
                    seen_alerts.add(alert_key)
                    alerts.append({
                        **{key: metric[key] for key in ("scope_type", "name", "resource", "unit", "period", "period_key", "actual", "limit", "percent_limit", "status")},
                        "recommendation": "Check meters and operating schedules; inspect high-load equipment, leaks, and after-hours usage.",
                    })
            elif metric["status"] == "Under Usage":
                alert_key = (metric["scope_type"], metric["name"], resource_name, latest_key, metric["status"])
                if alert_key not in seen_alerts:
                    seen_alerts.add(alert_key)
                    alerts.append({
                        **{key: metric[key] for key in ("scope_type", "name", "resource", "unit", "period", "period_key", "actual", "limit", "percent_limit", "status")},
                        "recommendation": "Verify occupancy, operating hours, and meter connectivity before treating the low reading as savings.",
                    })

        results[resource_name] = {
            "resource": resource_name,
            "unit": unit,
            "period": period,
            "period_key": latest_key,
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
            "university": university,
            "buildings": building_metrics,
            "departments": department_metrics,
            "historical_periods": len(period_keys),
            "trend": university["trend"],
        }

    selected = results.get(resource)
    university = selected["university"] if selected else None
    if university is not None:
        university["resource_summaries"] = [
            {
                "resource": name,
                "unit": result["unit"],
                "actual": result["university"]["actual"],
                "average": result["university"]["average"],
                "limit": result["university"]["limit"],
                "percent_limit": result["university"]["percent_limit"],
                "status": result["university"]["status"],
                "data_quality": result["university"]["data_quality"],
            }
            for name, result in sorted(results.items())
        ]

    return {
        "period": period,
        "resource": resource,
        "period_key": selected["period_key"] if selected else None,
        "as_of_date": selected["as_of_date"] if selected else None,
        "university": university,
        "buildings": selected["buildings"] if selected else [],
        "departments": selected["departments"] if selected else [],
        "resource_summaries": [
            {
                "resource": name,
                "unit": result["unit"],
                "actual": result["university"]["actual"],
                "average": result["university"]["average"],
                "limit": result["university"]["limit"],
                "percent_limit": result["university"]["percent_limit"],
                "status": result["university"]["status"],
                "data_quality": result["university"]["data_quality"],
            }
            for name, result in sorted(results.items())
        ],
        "alerts": alerts,
        "invalid_date_entries": (
            invalid_date_entries if resource == "all" else invalid_dates_by_resource.get(resource, 0)
        ),
        "invalid_unit_entries": (
            invalid_unit_entries if resource == "all" else invalid_units_by_resource.get(resource, 0)
        ),
        "limit_multiplier": limit_multiplier,
        "default_under_usage_ratio": default_under_usage_ratio,
    }


# ── Issue / Incident helpers ──────────────────────────────────────────

def get_all_issues(owner_user_id=None, department_names=None):
    conn = get_connection()
    try:
        query = "SELECT * FROM issues"
        clauses = []
        values = []
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            values.append(owner_user_id)
        if department_names is not None:
            names = list(department_names)
            if not names:
                return []
            clauses.append("department IN (" + ",".join("?" for _ in names) + ")")
            values.extend(names)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(query + " ORDER BY id DESC", values).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_issue(data):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO issues (title, category, area, description, priority, status, reporter, owner_user_id, department) "
            "VALUES (?, ?, ?, ?, ?, 'Reported', ?, ?, ?)",
            (
                data["title"],
                data.get("category", "other"),
                data["area"],
                data.get("description", ""),
                data.get("priority", "Medium"),
                data.get("reporter", "Student Volunteer"),
                data.get("owner_user_id"),
                data.get("department", ""),
            ),
        )
        issue_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_issue_status(issue_id, status):
    conn = get_connection()
    try:
        resolved_at = get_utc_now_iso() if status == "Resolved" else None
        cur = conn.execute(
            "UPDATE issues SET status = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_at, issue_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_issue(issue_id):
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Department Leaderboard Analytics ──────────────────────────────────

DEPARTMENTS = [
    {"name": "Computer Science & IT", "code": "CS", "building": "Tech Tower", "base_score": 920},
    {"name": "Mechanical Engineering", "code": "ME", "building": "West Wing", "base_score": 840},
    {"name": "Civil & Environmental", "code": "CE", "building": "Green Block", "base_score": 810},
    {"name": "Electronics & Comm.", "code": "EC", "building": "Science Complex", "base_score": 775},
    {"name": "Management & Arts", "code": "MBA", "building": "Central Block", "base_score": 730},
    {"name": "Hostel Block A & B", "code": "RES", "building": "Residential Zone", "base_score": 690},
]


def get_leaderboard(department_names=None):
    """
    Computes department sustainability rankings strictly adhering to formula:
    Points = Base Score + (Completed Actions * 15) + (Resolved Issues * 20) + (Logged Audits * 5)
    Results are dynamically ranked and badge-attributed based on total points.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM entries")
        total_entries = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM issues WHERE status = 'Resolved'")
        resolved_issues = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(DISTINCT action_idx) FROM actions WHERE completed = 1")
        actions_done = cur.fetchone()[0] or 0
    finally:
        conn.close()

    bonus_points = (actions_done * 15) + (resolved_issues * 20) + (total_entries * 5)

    scores = []
    badges = [
        "Eco Champion 🥇",
        "Energy Guardian 🥈",
        "Water Steward 🥉",
        "Waste Reducer 🌿",
        "Active Challenger ⚡",
        "Green Rising 🌱",
    ]

    for dept in DEPARTMENTS:
        pts = dept["base_score"] + bonus_points
        scores.append({
            "department": dept["name"],
            "code": dept["code"],
            "building": dept["building"],
            "points": max(100, pts),
            "co2_saved_kg": round((pts * 0.18), 1),
            "base_score": dept["base_score"]
        })

    if department_names is not None:
        allowed_departments = set(department_names)
        scores = [score for score in scores if score["department"] in allowed_departments]

    # Sort descending by points
    scores.sort(key=lambda x: x["points"], reverse=True)

    # Assign ranks and badges
    for i, s in enumerate(scores):
        s["rank"] = i + 1
        s["badge"] = badges[min(i, len(badges) - 1)]
        s["efficiency"] = f"{max(60, min(99, 96 - (i * 5)))}%"

    return scores


# ── AI Eco-Advisor Insights ───────────────────────────────────────────

def get_insights(department_names=None, owner_user_id=None):
    """
    Analyzes campus database and returns actionable insights,
    anomaly detection alerts, and potential financial savings.
    Safely handles empty database states and edge cases.
    """
    conn = get_connection()
    try:
        clauses = []
        params = []
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            params.append(owner_user_id)
        if department_names is not None:
            names = list(department_names)
            if not names:
                clauses.append("0")
            else:
                clauses.append("department IN (" + ",".join("?" for _ in names) + ")")
                params.extend(names)
        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        totals_rows = conn.execute(
            "SELECT resource, COALESCE(SUM(amount), 0) as sum_amt FROM entries"
            + where_clause + " GROUP BY resource",
            params,
        ).fetchall()
        t_map = {r["resource"]: float(r["sum_amt"]) for r in totals_rows}

        top_area_conditions = ["resource = 'electricity'"] + clauses
        top_area_row = conn.execute(
            "SELECT area, COALESCE(SUM(amount), 0) as amt FROM entries WHERE "
            + " AND ".join(top_area_conditions)
            + " GROUP BY area ORDER BY amt DESC LIMIT 1",
            params,
        ).fetchone()

        issue_clauses = ["status != 'Resolved'"]
        issue_params = []
        if owner_user_id is not None:
            issue_clauses.append("owner_user_id = ?")
            issue_params.append(owner_user_id)
        if department_names is not None:
            names = list(department_names)
            if not names:
                issue_clauses.append("0")
            else:
                issue_clauses.append("department IN (" + ",".join("?" for _ in names) + ")")
                issue_params.extend(names)
        cur_issues = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE " + " AND ".join(issue_clauses),
            issue_params,
        ).fetchone()
        pending_issues = cur_issues[0] if cur_issues else 0
    finally:
        conn.close()

    insights = []
    elec = t_map.get("electricity", 0.0)

    # 1. Electricity Insight & Anomaly Check
    if elec > 100.0:
        top_area = top_area_row["area"] if top_area_row else "Campus Labs"
        insights.append({
            "type": "warning",
            "category": "Electricity Spike",
            "title": f"High Consumption Detected in {top_area}",
            "detail": f"{top_area} represents the highest share of power consumption ({round(elec, 1)} kWh). Turning off idle lab equipment overnight can avoid up to ₹3,400 monthly.",
            "impact": "-18.5 kg CO₂/mo",
            "saving": "₹3,400"
        })
    elif elec > 0:
        insights.append({
            "type": "success",
            "category": "Energy Optimization",
            "title": "Electricity Consumption within Target",
            "detail": f"Current campus power usage ({round(elec, 1)} kWh) is keeping strictly within baseline parameters.",
            "impact": "On Track",
            "saving": "₹1,200"
        })
    else:
        insights.append({
            "type": "info",
            "category": "Data Baseline",
            "title": "Awaiting Electricity Telemetry",
            "detail": "Log meter readings from computer labs or lecture halls to activate automated energy benchmarking.",
            "impact": "Baseline Needed",
            "saving": "₹0"
        })

    # 2. Water Insight
    water = t_map.get("water", 0.0)
    if water > 500.0:
        insights.append({
            "type": "info",
            "category": "Water Conservation",
            "title": "Aerator & Sensor Tap Opportunity",
            "detail": f"Water consumption has reached {int(round(water))} L. Fitting flow aerators on washroom taps typically cuts flow by 35% without reducing comfort.",
            "impact": "-35% Water Flow",
            "saving": "₹1,850"
        })
    else:
        insights.append({
            "type": "success",
            "category": "Water Stewardship",
            "title": "Hydration Management Optimal",
            "detail": f"Campus water consumption ({int(round(water))} L) is within safe operational targets.",
            "impact": "Normal Flow",
            "saving": "₹650"
        })

    # 3. Open Hazard / Maintenance Impact
    if pending_issues > 0:
        insights.append({
            "type": "alert",
            "category": "Maintenance Impact",
            "title": f"{pending_issues} Unresolved Campus Hazard(s)",
            "detail": f"Resolving active leaks or unmonitored equipment prevents cumulative resource loss. Assign maintenance to close pending tickets.",
            "impact": "Immediate Win",
            "saving": "₹950"
        })
    else:
        insights.append({
            "type": "success",
            "category": "Zero Active Hazards",
            "title": "All Environmental Tickets Clear",
            "detail": "No open water leaks or equipment hazards currently reported. Excellent preventive maintenance!",
            "impact": "Hazard Free",
            "saving": "₹0"
        })

    # 4. Circular Economy / Recycling
    waste = t_map.get("waste", 0.0)
    insights.append({
        "type": "tip",
        "category": "Circular Economy",
        "title": "Canteen Organic Waste Segregation",
        "detail": f"{round(waste, 1)} kg segregated material recovered so far. Setting up campus compost bins for canteen peelings generates natural fertilizer for campus grounds.",
        "impact": "+45 kg Compost",
        "saving": "₹800"
    })

    return insights
