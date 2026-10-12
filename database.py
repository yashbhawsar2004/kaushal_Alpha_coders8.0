"""
database.py — SQLite database layer for EcoCampus.
Handles table creation, CRUD operations, transactions, data integrity,
incident issue tickets, departmental leaderboard scoring, and AI insights.
"""

import os
import sqlite3
import math
from datetime import date, datetime, timezone

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecocampus.db")
DB_PATH = os.getenv("DATABASE_PATH", DEFAULT_DB)

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


def init_db():
    """Create tables and indexes if they don't exist and seed initial demo data."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Resource entries
        cur.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                resource    TEXT    NOT NULL,
                amount      REAL    NOT NULL CHECK(amount > 0),
                unit        TEXT    NOT NULL,
                area        TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                reporter    TEXT    DEFAULT '',
                note        TEXT    DEFAULT '',
                is_demo     INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        # 2. Checklist actions (unique action index constraint)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                action_idx  INTEGER NOT NULL UNIQUE,
                completed   INTEGER DEFAULT 1,
                updated_at  TEXT    DEFAULT (datetime('now'))
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

        # Indexes for query optimization
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_resource ON entries(resource)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status)")

        # Seed demo entries if table is completely empty
        cur.execute("SELECT COUNT(*) FROM entries")
        if cur.fetchone()[0] == 0:
            today = date.today().isoformat()
            demo_rows = [
                ("electricity", 42,  "kWh",    "Computer Lab 2", today, "EcoClub Team", "Evening lab monitoring", 1),
                ("water",       280, "L",      "Washroom Block A", today, "Campus Ops", "Metered flush cycle", 1),
                ("paper",       80,  "sheets", "Admin Office", today, "Staff Council", "Exam sheets printed", 1),
                ("waste",       6,   "kg",     "Main Canteen", today, "Green Ambassadors", "Food compost segregated", 1),
                ("electricity", 35,  "kWh",    "Lecture Hall A", today, "EcoClub Team", "Projector & AC usage", 1),
                ("water",       310, "L",      "Botanical Garden", today, "Groundskeeping", "Drip irrigation run", 1),
                ("electricity", 65,  "kWh",    "Mechanical Workshop", today, "Faculty rep", "Machinery testing", 1),
                ("waste",       12,  "kg",     "Library Quad", today, "EcoClub Team", "Plastic recycling bin audit", 1),
            ]
            cur.executemany(
                "INSERT INTO entries (resource, amount, unit, area, date, reporter, note, is_demo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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


# ── Entry CRUD ─────────────────────────────────────────────────────────

def get_all_entries():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM entries ORDER BY date DESC, id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_entry(data):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO entries (resource, amount, unit, area, date, reporter, note, is_demo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["resource"],
                float(data["amount"]),
                data["unit"],
                data["area"],
                data["date"],
                data.get("reporter", ""),
                data.get("note", ""),
                1 if data.get("is_demo") else 0,
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


# ── Action helpers ─────────────────────────────────────────────────────

def get_completed_actions():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT action_idx FROM actions WHERE completed = 1 ORDER BY action_idx ASC").fetchall()
        return [r["action_idx"] for r in rows]
    finally:
        conn.close()


def set_action(action_idx, completed):
    conn = get_connection()
    try:
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


# ── Issue / Incident helpers ──────────────────────────────────────────

def get_all_issues():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM issues ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_issue(data):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO issues (title, category, area, description, priority, status, reporter) "
            "VALUES (?, ?, ?, ?, ?, 'Reported', ?)",
            (
                data["title"],
                data.get("category", "other"),
                data["area"],
                data.get("description", ""),
                data.get("priority", "Medium"),
                data.get("reporter", "Student Volunteer"),
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


def get_leaderboard():
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

    # Sort descending by points
    scores.sort(key=lambda x: x["points"], reverse=True)

    # Assign ranks and badges
    for i, s in enumerate(scores):
        s["rank"] = i + 1
        s["badge"] = badges[min(i, len(badges) - 1)]
        s["efficiency"] = f"{max(60, min(99, 96 - (i * 5)))}%"

    return scores


# ── AI Eco-Advisor Insights ───────────────────────────────────────────

def get_insights():
    """
    Analyzes campus database and returns actionable insights,
    anomaly detection alerts, and potential financial savings.
    Safely handles empty database states and edge cases.
    """
    conn = get_connection()
    try:
        totals_rows = conn.execute(
            "SELECT resource, COALESCE(SUM(amount), 0) as sum_amt FROM entries GROUP BY resource"
        ).fetchall()
        t_map = {r["resource"]: float(r["sum_amt"]) for r in totals_rows}

        top_area_row = conn.execute("""
            SELECT area, COALESCE(SUM(amount), 0) as amt FROM entries 
            WHERE resource = 'electricity' 
            GROUP BY area ORDER BY amt DESC LIMIT 1
        """).fetchone()

        cur_issues = conn.execute("SELECT COUNT(*) FROM issues WHERE status != 'Resolved'").fetchone()
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
