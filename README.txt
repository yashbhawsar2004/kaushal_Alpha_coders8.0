# EcoCampus — Sustainability & Resource Hub

An intelligent, full-stack campus sustainability platform that tracks utilities, crowdsources hazard reporting, gamifies departmental conservation, and provides data-driven AI recommendations.

## Quick Start
1. Open a terminal in the project folder:
   cd d:\kaushal\ecocampus_mvp

2. Install dependencies:
   pip install -r requirements.txt

3. Run verification test suite:
   python audit_verifier.py
   (Runs 10/10 automated tests against database, APIs, security, and edge cases)

4. Start the server:
   python server.py

5. Open your browser:
   http://localhost:5501

## Features Included
- Resource Metering: Log electricity (kWh), water (L), paper, and waste (kg).
- Departmental Leaderboard: Dynamic rankings across campus faculties with eco-points.
- Incident Reporting Desk: Report leaking taps, idle ACs, and manage ticket status.
- AI Eco-Advisor: Algorithmic anomaly alerts, savings calculations (in ₹ and CO2).
- IoT Telemetry Webhook: Smart meter integration via POST /api/sensors/ingest.
- Printable Executive Audit Report: One-click print/PDF report for accreditation.
- Security Hardened: Mitigation for CSV formula injection (CWE-1236) and HTTP security headers.
- Data Persistence: SQLite database (ecocampus.db) with WAL mode.
- Cloud & Docker Ready: Dockerfile, Procfile, and Render configuration included.

For complete documentation, architecture diagrams, and REST API reference, please refer to README.md and DEPLOYMENT.md.
