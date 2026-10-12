"""
database.py — SQLite database layer for EcoCampus.
Handles table creation, CRUD operations, transactions, data integrity,
incident issue tickets, departmental leaderboard scoring, and AI insights.
"""

import os
import sqlite3
import math
from datetime import date, datetime, timezone, timedelta

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

        # 4. Building consumption limits
        cur.execute("""
            CREATE TABLE IF NOT EXISTS building_limits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                building        TEXT    NOT NULL,
                resource        TEXT    NOT NULL,
                max_limit       REAL    NOT NULL DEFAULT 100,
                min_threshold   REAL    NOT NULL DEFAULT 20,
                area_sqft       REAL    DEFAULT 0,
                occupancy       INTEGER DEFAULT 0,
                created_at      TEXT    DEFAULT (datetime('now')),
                updated_at      TEXT    DEFAULT (datetime('now')),
                UNIQUE(building, resource)
            )
        """)

        # Indexes for query optimization
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_resource ON entries(resource)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_area ON entries(area)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_limits_building ON building_limits(building, resource)")

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

        # Seed default building limits if table is empty
        cur.execute("SELECT COUNT(*) FROM building_limits")
        if cur.fetchone()[0] == 0:
            default_limits = [
                # Building                Resource       MaxLimit  MinThreshold  AreaSqft  Occupancy
                ("Computer Lab 2",        "electricity", 80,       10,           1200,     60),
                ("Lecture Hall A",         "electricity", 70,       8,            2000,     120),
                ("Mechanical Workshop",    "electricity", 100,      15,           3000,     40),
                ("Washroom Block A",       "water",       400,      50,           800,      200),
                ("Botanical Garden",       "water",       500,      80,           5000,     10),
                ("Admin Office",           "paper",       150,      10,           600,      25),
                ("Main Canteen",           "waste",       25,       2,            1500,     300),
                ("Library Quad",           "waste",       20,       2,            400,      50),
                ("Seminar Hall B",         "electricity", 60,       5,            1800,     150),
                ("Main Library",           "electricity", 55,       8,            2500,     100),
                ("Computer Lab 1",         "electricity", 85,       10,           1400,     65),
                ("Science Complex",        "electricity", 90,       12,           3500,     200),
                ("Central Block",          "electricity", 75,       10,           2200,     180),
                ("Residential Zone",       "electricity", 120,      20,           8000,     400),
                ("Washroom Block A",       "electricity", 30,       3,            800,      200),
                ("Computer Lab 2",         "water",       150,      15,           1200,     60),
                ("Main Canteen",           "electricity", 50,       5,            1500,     300),
                ("Main Canteen",           "water",       600,      50,           1500,     300),
                ("Library Quad",           "electricity", 40,       5,            400,      50),
            ]
            cur.executemany(
                "INSERT OR IGNORE INTO building_limits "
                "(building, resource, max_limit, min_threshold, area_sqft, occupancy) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                default_limits,
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


# ── Resource Consumption Monitoring & Analytics ──────────────────────────

# Default monthly limits used when a building has no specific configured limit
DEFAULT_LIMITS = {
    "electricity": {"max": 80, "min": 10, "unit": "kWh"},
    "water":       {"max": 400, "min": 50, "unit": "L"},
    "paper":       {"max": 150, "min": 10, "unit": "sheets"},
    "waste":       {"max": 30, "min": 3, "unit": "kg"},
    "generalWaste":{"max": 25, "min": 2, "unit": "kg"},
}

BUILDING_DEPARTMENTS = {
    "Computer Lab 2": "Computer Science & IT",
    "Computer Lab 1": "Computer Science & IT",
    "Lecture Hall A": "Faculty of Engineering",
    "Mechanical Workshop": "Mechanical Engineering",
    "Washroom Block A": "Campus Facilities & Ops",
    "Botanical Garden": "Environmental Sciences",
    "Admin Office": "Central Administration",
    "Main Canteen": "Hospitality & Campus Services",
    "Library Quad": "Library & Student Affairs",
    "Seminar Hall B": "School of Management",
    "Main Library": "Library & Academic Resources",
    "Science Complex": "Faculty of Natural Sciences",
    "Central Block": "General Academics",
    "Residential Zone": "Student Housing & Hostels",
}


def get_building_department(building_name):
    """Map a building or campus area to its corresponding department."""
    if not building_name:
        return "General Campus"
    return BUILDING_DEPARTMENTS.get(building_name, "General Campus")


def get_building_limits():
    """Return all configured building consumption limits."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM building_limits ORDER BY building, resource"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_building_limit(building, resource, max_limit, min_threshold=None,
                       area_sqft=None, occupancy=None):
    """
    Create or update a consumption limit for a building/department + resource combination.
    Uses UPSERT (INSERT ... ON CONFLICT ... DO UPDATE) for atomicity.
    """
    conn = get_connection()
    try:
        defaults = DEFAULT_LIMITS.get(resource, {"max": 100, "min": 20})
        if min_threshold is None:
            min_threshold = defaults["min"]

        conn.execute(
            "INSERT INTO building_limits (building, resource, max_limit, min_threshold, area_sqft, occupancy, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(building, resource) DO UPDATE SET "
            "max_limit=excluded.max_limit, min_threshold=excluded.min_threshold, "
            "area_sqft=COALESCE(excluded.area_sqft, area_sqft), "
            "occupancy=COALESCE(excluded.occupancy, occupancy), "
            "updated_at=excluded.updated_at",
            (building, resource, float(max_limit), float(min_threshold),
             float(area_sqft or 0), int(occupancy or 0), get_utc_now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM building_limits WHERE building = ? AND resource = ?",
            (building, resource),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_consumption_analytics(period="monthly", resource_filter=None):
    """
    Compute building-wise, department-wise, and university-wide consumption analytics.

    period: 'daily' (today), 'weekly' (last 7 days), 'monthly' (last 30 days)
    resource_filter: None / 'all' (every resource) or a specific resource type

    Returns a dict with:
      - period metadata (name, num_days, start_date, end_date)
      - buildings: list of building analytics with per-resource metrics, status, normalization
      - departments: aggregated metrics by department
      - university: aggregated university-wide totals with status and averages
      - daily_trends: chronological trend data per day for charts
      - alerts: actionable warnings and recommendations for consumption anomalies
    """
    conn = get_connection()
    try:
        today_date = date.today()
        if period == "daily":
            start_date = today_date.isoformat()
            num_days = 1
            period_label = "Daily (Today)"
        elif period == "weekly":
            start_date = (today_date - timedelta(days=6)).isoformat()
            num_days = 7
            period_label = "Weekly (Last 7 Days)"
        else:  # monthly
            start_date = (today_date - timedelta(days=29)).isoformat()
            num_days = 30
            period_label = "Monthly (Last 30 Days)"

        end_date = today_date.isoformat()
        limit_scale = num_days / 30.0

        # ── 1. Fetch Configured Limits ──────────────────────────────
        limits_rows = conn.execute("SELECT * FROM building_limits").fetchall()
        limits_map = {}
        configured_buildings = set()
        for lr in limits_rows:
            limits_map[(lr["building"], lr["resource"])] = dict(lr)
            if lr["building"] != "University-Wide":
                configured_buildings.add(lr["building"])

        # Also collect all known areas from historical entries
        known_areas_rows = conn.execute("SELECT DISTINCT area FROM entries").fetchall()
        for r in known_areas_rows:
            if r["area"]:
                configured_buildings.add(r["area"])

        # ── 2. Fetch Period Consumption ─────────────────────────────
        query = """
            SELECT area AS building, resource,
                   SUM(amount)   AS total_consumption,
                   AVG(amount)   AS avg_per_entry,
                   COUNT(*)      AS entry_count,
                   MIN(date)     AS first_entry,
                   MAX(date)     AS last_entry
            FROM entries
            WHERE date >= ? AND date <= ?
        """
        params = [start_date, end_date]
        if resource_filter and resource_filter != "all":
            query += " AND resource = ?"
            params.append(resource_filter)

        query += " GROUP BY area, resource ORDER BY total_consumption DESC"
        rows = conn.execute(query, params).fetchall()

        period_data_map = {}
        for row in rows:
            period_data_map[(row["building"], row["resource"])] = dict(row)

        # ── 3. Fetch Historical Overall Averages for Baseline ────────
        hist_rows = conn.execute("""
            SELECT area AS building, resource,
                   AVG(amount) AS hist_avg_entry,
                   SUM(amount) AS hist_total,
                   COUNT(*) AS hist_count,
                   MAX(date) AS latest_entry_date
            FROM entries
            GROUP BY area, resource
        """).fetchall()
        hist_map = {}
        for hr in hist_rows:
            hist_map[(hr["building"], hr["resource"])] = dict(hr)

        # ── 4. Build Comprehensive Building-Wise Analytics ───────────
        target_resources = (
            [resource_filter] if (resource_filter and resource_filter != "all")
            else ["electricity", "water", "paper", "waste"]
        )

        buildings_map = {}

        for bldg in sorted(configured_buildings):
            dept = get_building_department(bldg)
            # Find building metadata (area_sqft, occupancy) from limits_map if available
            bldg_sqft = 0
            bldg_occ = 0
            for rk in target_resources:
                if (bldg, rk) in limits_map:
                    cfg = limits_map[(bldg, rk)]
                    if cfg.get("area_sqft") and cfg["area_sqft"] > 0:
                        bldg_sqft = cfg["area_sqft"]
                    if cfg.get("occupancy") and cfg["occupancy"] > 0:
                        bldg_occ = cfg["occupancy"]

            buildings_map[bldg] = {
                "building": bldg,
                "department": dept,
                "area_sqft": bldg_sqft,
                "occupancy": bldg_occ,
                "resources": {},
            }

            for res in target_resources:
                row = period_data_map.get((bldg, res))
                hist = hist_map.get((bldg, res))
                limit_cfg = limits_map.get((bldg, res))

                # Defaults if not configured
                defaults = DEFAULT_LIMITS.get(res, {"max": 100, "min": 20})
                if limit_cfg:
                    monthly_max = float(limit_cfg["max_limit"])
                    monthly_min = float(limit_cfg["min_threshold"])
                    has_custom_limit = True
                else:
                    monthly_max = defaults["max"]
                    monthly_min = defaults["min"]
                    has_custom_limit = False

                scaled_max = round(monthly_max * limit_scale, 2)
                scaled_min = round(monthly_min * limit_scale, 2)

                if row:
                    total = round(float(row["total_consumption"]), 2)
                    entry_count = int(row["entry_count"])
                    avg_entry = round(float(row["avg_per_entry"]), 2)
                    avg_daily = round(total / max(num_days, 1), 2)
                    has_data = True
                    is_baseline = False
                    first_date = row["first_entry"]
                    last_date = row["last_entry"]
                else:
                    total = 0.0
                    entry_count = 0
                    has_data = False
                    is_baseline = True
                    avg_daily = 0.0
                    if hist:
                        avg_entry = round(float(hist["hist_avg_entry"]), 2)
                        first_date = None
                        last_date = hist["latest_entry_date"]
                    else:
                        avg_entry = 0.0
                        first_date = None
                        last_date = None

                pct_of_limit = round((total / scaled_max * 100), 1) if scaled_max > 0 else 0
                diff_from_avg = round(total - (avg_daily * num_days if has_data else avg_entry), 2)

                # Status Classification:
                # - Over: total > scaled_max (Red)
                # - Under: total < scaled_min (Blue)
                # - Within: scaled_min <= total <= scaled_max (Green)
                if total > scaled_max:
                    status = "over"
                    status_label = "Over Consumption"
                elif total < scaled_min:
                    status = "under"
                    status_label = "Under Usage"
                else:
                    status = "within"
                    status_label = "Within Limit"

                # Fair Comparison Normalization
                norm_sqft = round((total / bldg_sqft * 1000), 2) if bldg_sqft > 0 else None
                norm_occ = round(total / bldg_occ, 2) if bldg_occ > 0 else None

                buildings_map[bldg]["resources"][res] = {
                    "resource": res,
                    "unit": DEFAULT_LIMITS.get(res, {}).get("unit", ""),
                    "total": total,
                    "average_daily": avg_daily,
                    "avg_per_entry": avg_entry,
                    "entry_count": entry_count,
                    "has_data": has_data,
                    "is_baseline": is_baseline,
                    "monthly_max_limit": monthly_max,
                    "monthly_min_threshold": monthly_min,
                    "scaled_max_limit": scaled_max,
                    "scaled_min_threshold": scaled_min,
                    "pct_of_limit": pct_of_limit,
                    "diff_from_avg": diff_from_avg,
                    "status": status,
                    "status_label": status_label,
                    "has_custom_limit": has_custom_limit,
                    "first_entry": first_date,
                    "last_entry": last_date,
                    "normalized_per_1k_sqft": norm_sqft,
                    "normalized_per_occupant": norm_occ,
                }

        buildings_list = list(buildings_map.values())

        # ── 5. Department-Wise Aggregation ───────────────────────────
        departments_map = {}
        for bldg_data in buildings_list:
            dept = bldg_data["department"]
            if dept not in departments_map:
                departments_map[dept] = {
                    "department": dept,
                    "buildings": [],
                    "resources": {},
                }
            departments_map[dept]["buildings"].append(bldg_data["building"])

            for res, data in bldg_data["resources"].items():
                if res not in departments_map[dept]["resources"]:
                    departments_map[dept]["resources"][res] = {
                        "resource": res,
                        "unit": data["unit"],
                        "total": 0.0,
                        "scaled_limit_sum": 0.0,
                        "building_count": 0,
                        "active_building_count": 0,
                    }
                dr = departments_map[dept]["resources"][res]
                dr["total"] = round(dr["total"] + data["total"], 2)
                dr["scaled_limit_sum"] = round(dr["scaled_limit_sum"] + data["scaled_max_limit"], 2)
                dr["building_count"] += 1
                if data["has_data"]:
                    dr["active_building_count"] += 1

        departments_list = []
        for dept, ddata in departments_map.items():
            for res, rdata in ddata["resources"].items():
                rdata["avg_per_building"] = round(rdata["total"] / max(rdata["building_count"], 1), 2)
                rdata["average_daily"] = round(rdata["total"] / max(num_days, 1), 2)
                rdata["pct_of_limit"] = round(
                    rdata["total"] / rdata["scaled_limit_sum"] * 100, 1
                ) if rdata["scaled_limit_sum"] > 0 else 0
                if rdata["total"] > rdata["scaled_limit_sum"]:
                    rdata["status"] = "over"
                    rdata["status_label"] = "Over Consumption"
                elif rdata["pct_of_limit"] < 30 or rdata["total"] == 0:
                    rdata["status"] = "under"
                    rdata["status_label"] = "Under Usage"
                else:
                    rdata["status"] = "within"
                    rdata["status_label"] = "Within Limit"
            departments_list.append(ddata)

        # ── 6. University-Wide Aggregation ───────────────────────────
        uni_totals = {}
        for bldg_data in buildings_list:
            for res, data in bldg_data["resources"].items():
                if res not in uni_totals:
                    # Check if there is an explicit university-wide configured limit
                    uni_cfg = limits_map.get(("University-Wide", res))
                    uni_monthly_max = float(uni_cfg["max_limit"]) if uni_cfg else None

                    uni_totals[res] = {
                        "resource": res,
                        "unit": data["unit"],
                        "total": 0.0,
                        "building_count": 0,
                        "active_building_count": 0,
                        "total_entries": 0,
                        "over_count": 0,
                        "within_count": 0,
                        "under_count": 0,
                        "scaled_limit_sum": 0.0,
                        "custom_uni_limit": uni_monthly_max is not None,
                        "configured_uni_monthly_limit": uni_monthly_max,
                    }
                u = uni_totals[res]
                u["total"] = round(u["total"] + data["total"], 2)
                u["building_count"] += 1
                if data["has_data"]:
                    u["active_building_count"] += 1
                u["total_entries"] += data["entry_count"]
                u["scaled_limit_sum"] = round(u["scaled_limit_sum"] + data["scaled_max_limit"], 2)
                if data["status"] == "over":
                    u["over_count"] += 1
                elif data["status"] == "within":
                    u["within_count"] += 1
                else:
                    u["under_count"] += 1

        for res, u in uni_totals.items():
            # Use explicit university limit if configured, otherwise sum of buildings
            if u["custom_uni_limit"] and u["configured_uni_monthly_limit"]:
                effective_limit = round(u["configured_uni_monthly_limit"] * limit_scale, 2)
            else:
                effective_limit = u["scaled_limit_sum"]

            u["effective_limit"] = effective_limit
            u["average_daily"] = round(u["total"] / max(num_days, 1), 2)
            u["avg_per_building"] = round(u["total"] / max(u["building_count"], 1), 2)
            u["pct_of_limit"] = round(
                u["total"] / effective_limit * 100, 1
            ) if effective_limit > 0 else 0

            # Consolidated university status
            if u["total"] > effective_limit:
                u["status"] = "over"
                u["status_label"] = "Over Consumption"
            elif u["pct_of_limit"] < 30:
                u["status"] = "under"
                u["status_label"] = "Under Usage"
            else:
                u["status"] = "within"
                u["status_label"] = "Within Limit"

        # ── 7. Highest and Lowest Consuming Buildings ─────────────────
        highest_building = None
        lowest_building = None
        active_buildings = [b for b in buildings_list if any(r["total"] > 0 for r in b["resources"].values())]

        if active_buildings:
            sorted_active = sorted(
                active_buildings,
                key=lambda b: sum(d["total"] for d in b["resources"].values()),
                reverse=True,
            )
            highest_building = {
                "building": sorted_active[0]["building"],
                "department": sorted_active[0]["department"],
                "total": round(sum(d["total"] for d in sorted_active[0]["resources"].values()), 2),
            }
            lowest_building = {
                "building": sorted_active[-1]["building"],
                "department": sorted_active[-1]["department"],
                "total": round(sum(d["total"] for d in sorted_active[-1]["resources"].values()), 2),
            }

        # ── 8. Daily Historical Trends for Graphs ────────────────────
        trend_query = """
            SELECT date, resource, SUM(amount) as daily_total
            FROM entries
            WHERE date >= ? AND date <= ?
            GROUP BY date, resource
            ORDER BY date ASC
        """
        trend_rows = conn.execute(trend_query, [start_date, end_date]).fetchall()
        daily_trends = {}
        for tr in trend_rows:
            d_str = tr["date"]
            if d_str not in daily_trends:
                daily_trends[d_str] = {}
            daily_trends[d_str][tr["resource"]] = round(float(tr["daily_total"]), 2)

        # ── 9. Deduplicated Actionable Alerts ─────────────────────────
        alerts = []
        for bldg_data in buildings_list:
            for res, data in bldg_data["resources"].items():
                res_label = {"electricity": "Electricity", "water": "Water",
                             "paper": "Paper", "waste": "Waste"}.get(res, res.capitalize())
                res_unit = data["unit"]

                if data["status"] == "over":
                    excess = round(data["total"] - data["scaled_max_limit"], 2)
                    alerts.append({
                        "id": f"alert-over-{bldg_data['building']}-{res}",
                        "type": "over",
                        "severity": "high" if data["pct_of_limit"] > 140 else "medium",
                        "building": bldg_data["building"],
                        "department": bldg_data["department"],
                        "resource": res,
                        "title": f"{bldg_data['building']} ({bldg_data['department']}): {res_label} Exceeds Limit",
                        "detail": f"Actual consumption reached {data['total']} {res_unit}, exceeding the {period_label.lower()} limit of {data['scaled_max_limit']} {res_unit} by +{excess} {res_unit} ({data['pct_of_limit']}% capacity).",
                        "recommendation": f"Schedule immediate energy/water audit for {bldg_data['building']}. Enforce automated shut-offs during non-operational hours and check high-load equipment.",
                    })
                elif data["status"] == "under" and not data["has_data"]:
                    alerts.append({
                        "id": f"alert-nodata-{bldg_data['building']}-{res}",
                        "type": "missing",
                        "severity": "low",
                        "building": bldg_data["building"],
                        "department": bldg_data["department"],
                        "resource": res,
                        "title": f"{bldg_data['building']}: Missing {res_label} Readings",
                        "detail": f"No telemetry or manual logs recorded for {bldg_data['building']} in the {period_label.lower()} window. Baseline threshold is {data['scaled_min_threshold']} {res_unit}.",
                        "recommendation": f"Verify IoT meter status or request field audit in {bldg_data['building']} to resume accurate tracking.",
                    })

        # University-wide limit alert if overall is over limit
        for res, u in uni_totals.items():
            if u["status"] == "over":
                res_label = {"electricity": "Electricity", "water": "Water",
                             "paper": "Paper", "waste": "Waste"}.get(res, res.capitalize())
                excess = round(u["total"] - u["effective_limit"], 2)
                alerts.append({
                    "id": f"alert-uni-over-{res}",
                    "type": "over",
                    "severity": "high",
                    "building": "Campus-Wide",
                    "department": "All Faculties",
                    "resource": res,
                    "title": f"University-Wide {res_label} Limit Breached",
                    "detail": f"Campus consumed {u['total']} {u['unit']} against overall ceiling of {u['effective_limit']} {u['unit']} (+{excess} {u['unit']}).",
                    "recommendation": "Initiate campus-wide conservation protocol. Review top offending facilities.",
                })

        return {
            "period": period,
            "period_label": period_label,
            "num_days": num_days,
            "start_date": start_date,
            "end_date": end_date,
            "buildings": buildings_list,
            "departments": departments_list,
            "university": uni_totals,
            "highest_building": highest_building,
            "lowest_building": lowest_building,
            "total_buildings": len(buildings_list),
            "daily_trends": daily_trends,
            "alerts": alerts,
        }
    finally:
        conn.close()
