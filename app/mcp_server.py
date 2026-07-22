"""
Elderly Care Assistant — MCP Server (stdio transport)
Exposes 5 domain tools for medication, wellness, and caregiver coordination.
"""

import json
import sqlite3
import os
from datetime import datetime, date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── In-memory/local storage for demo (uses SQLite file) ──────────────────────
DB_PATH = Path(__file__).parent.parent / "elderly_care_data.db"


def get_db():
    """Get a SQLite connection, creating tables if needed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            value TEXT NOT NULL,
            unit TEXT,
            recorded_at TEXT NOT NULL,
            notes TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            medication_name TEXT NOT NULL,
            dose TEXT NOT NULL,
            frequency TEXT NOT NULL,
            time_of_day TEXT NOT NULL,
            with_food INTEGER DEFAULT 0,
            start_date TEXT,
            notes TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            reminder_type TEXT NOT NULL,
            message TEXT NOT NULL,
            due_at TEXT NOT NULL,
            completed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


# ── MCP Server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="elderly-care-mcp",
    instructions="MCP server providing health tracking, medication management, and wellness tools for elderly care.",
)


@mcp.tool()
def log_health_metric(
    patient_name: str,
    metric_type: str,
    value: str,
    unit: str = "",
    notes: str = "",
) -> str:
    """
    Log a health metric for a patient (blood pressure, glucose, weight, mood, pain level, etc.).

    Args:
        patient_name: Name of the patient.
        metric_type: Type of metric (e.g. 'blood_pressure', 'glucose', 'weight', 'mood', 'pain_level', 'temperature').
        value: The measured value as a string (e.g. '120/80', '5.4', '72kg', '7/10').
        unit: Unit of measurement (e.g. 'mmHg', 'mmol/L', 'kg', 'scale 1-10').
        notes: Optional observations or context.

    Returns:
        Confirmation message with the logged entry.
    """
    recorded_at = datetime.utcnow().isoformat() + "Z"
    conn = get_db()
    conn.execute(
        "INSERT INTO health_metrics (patient_name, metric_type, value, unit, recorded_at, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (patient_name, metric_type, value, unit, recorded_at, notes),
    )
    conn.commit()
    conn.close()

    # Simple alert thresholds
    alerts = []
    if metric_type == "blood_pressure":
        try:
            systolic = int(value.split("/")[0])
            if systolic > 140:
                alerts.append("⚠️ High blood pressure detected — please rest and consult your doctor if it persists.")
            elif systolic < 90:
                alerts.append("⚠️ Low blood pressure detected — sit down carefully and call for help if you feel faint.")
        except Exception:
            pass
    if metric_type == "pain_level":
        try:
            pain = int(value.split("/")[0])
            if pain >= 7:
                alerts.append("⚠️ High pain level reported — please contact your caregiver or doctor.")
        except Exception:
            pass

    result = {
        "status": "logged",
        "patient": patient_name,
        "metric": metric_type,
        "value": f"{value} {unit}".strip(),
        "recorded_at": recorded_at,
        "alerts": alerts,
    }
    return json.dumps(result)


@mcp.tool()
def get_medication_schedule(patient_name: str) -> str:
    """
    Retrieve the current medication schedule for a patient.

    Args:
        patient_name: Name of the patient.

    Returns:
        JSON list of all medications with dose, frequency, and timing details.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM medications WHERE patient_name = ? ORDER BY time_of_day",
        (patient_name,),
    ).fetchall()
    conn.close()

    if not rows:
        # Seed with example data for demo purposes
        conn = get_db()
        sample_meds = [
            (patient_name, "Metformin", "500mg", "twice daily", "Morning & Evening", 1, str(date.today()), "Take with breakfast and dinner"),
            (patient_name, "Atorvastatin", "20mg", "once daily", "Evening", 0, str(date.today()), "Take at bedtime"),
            (patient_name, "Amlodipine", "5mg", "once daily", "Morning", 0, str(date.today()), "Blood pressure medication"),
        ]
        conn.executemany(
            "INSERT INTO medications (patient_name, medication_name, dose, frequency, time_of_day, with_food, start_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            sample_meds,
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM medications WHERE patient_name = ? ORDER BY time_of_day",
            (patient_name,),
        ).fetchall()
        conn.close()

    medications = [dict(row) for row in rows]
    return json.dumps({"patient": patient_name, "medications": medications, "count": len(medications)})


@mcp.tool()
def add_medication_reminder(
    patient_name: str,
    medication_name: str,
    dose: str,
    frequency: str,
    time_of_day: str,
    with_food: bool = False,
    notes: str = "",
) -> str:
    """
    Add a new medication to a patient's schedule and set a reminder.

    Args:
        patient_name: Name of the patient.
        medication_name: Name of the medication (e.g. 'Metformin').
        dose: Dose amount and unit (e.g. '500mg', '10ml').
        frequency: How often to take (e.g. 'once daily', 'twice daily', 'every 8 hours').
        time_of_day: When to take it (e.g. 'Morning', 'Evening', 'Morning & Evening').
        with_food: Whether medication must be taken with food.
        notes: Any additional instructions.

    Returns:
        Confirmation of the added medication reminder.
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO medications (patient_name, medication_name, dose, frequency, time_of_day, with_food, start_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (patient_name, medication_name, dose, frequency, time_of_day, int(with_food), str(date.today()), notes),
    )
    conn.commit()
    conn.close()

    return json.dumps({
        "status": "added",
        "patient": patient_name,
        "medication": medication_name,
        "dose": dose,
        "frequency": frequency,
        "time_of_day": time_of_day,
        "with_food": with_food,
        "notes": notes,
        "message": f"✅ Reminder set for {medication_name} {dose} — {frequency} at {time_of_day}.",
    })


@mcp.tool()
def get_wellness_summary(patient_name: str, days: int = 7) -> str:
    """
    Retrieve a wellness summary for the last N days, showing trends across all logged metrics.

    Args:
        patient_name: Name of the patient.
        days: Number of days to look back (default: 7).

    Returns:
        JSON summary of health metrics over the specified period, with trend notes.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT metric_type, value, unit, recorded_at, notes
           FROM health_metrics
           WHERE patient_name = ?
           ORDER BY recorded_at DESC
           LIMIT ?""",
        (patient_name, days * 10),  # rough cap
    ).fetchall()
    conn.close()

    if not rows:
        return json.dumps({
            "patient": patient_name,
            "period_days": days,
            "summary": "No health metrics recorded yet. Start logging to see your wellness summary.",
            "metrics": [],
        })

    # Group by metric type
    grouped: dict = {}
    for row in rows:
        mtype = row["metric_type"]
        if mtype not in grouped:
            grouped[mtype] = []
        grouped[mtype].append({
            "value": row["value"],
            "unit": row["unit"],
            "recorded_at": row["recorded_at"],
            "notes": row["notes"],
        })

    summary_parts = []
    for mtype, entries in grouped.items():
        summary_parts.append({
            "metric": mtype,
            "latest": entries[0]["value"] + (" " + entries[0]["unit"]).rstrip(),
            "recorded_at": entries[0]["recorded_at"],
            "count": len(entries),
        })

    return json.dumps({
        "patient": patient_name,
        "period_days": days,
        "metrics_tracked": len(grouped),
        "summary": summary_parts,
        "total_entries": len(rows),
    })


@mcp.tool()
def check_drug_interaction(medication_a: str, medication_b: str) -> str:
    """
    Check for known interactions between two medications.

    Args:
        medication_a: First medication name.
        medication_b: Second medication name.

    Returns:
        Interaction risk level and advisory note. Always recommend consulting a doctor.
    """
    # Simplified interaction database for demo — real deployment would use a medical API
    INTERACTIONS: dict[frozenset, dict] = {
        frozenset({"warfarin", "aspirin"}): {
            "risk": "HIGH",
            "effect": "Increased bleeding risk when combining blood thinners.",
            "advice": "Consult your doctor before taking both — dose adjustment may be needed.",
        },
        frozenset({"metformin", "alcohol"}): {
            "risk": "MODERATE",
            "effect": "Alcohol can lower blood sugar and increase lactic acidosis risk.",
            "advice": "Avoid alcohol while taking Metformin. Inform your doctor if you drink regularly.",
        },
        frozenset({"atorvastatin", "grapefruit"}): {
            "risk": "MODERATE",
            "effect": "Grapefruit can increase statin levels in your blood, raising side-effect risk.",
            "advice": "Avoid grapefruit juice while taking Atorvastatin.",
        },
        frozenset({"amlodipine", "simvastatin"}): {
            "risk": "MODERATE",
            "effect": "Amlodipine can increase Simvastatin blood levels, raising muscle-pain risk.",
            "advice": "Your doctor may lower the Simvastatin dose. Report any muscle aches immediately.",
        },
        frozenset({"ssri", "tramadol"}): {
            "risk": "HIGH",
            "effect": "Risk of serotonin syndrome — a potentially serious condition.",
            "advice": "Do not combine without explicit doctor approval. Seek immediate help if you feel confused, have a fast heartbeat, or muscle twitches.",
        },
    }

    key = frozenset({medication_a.lower(), medication_b.lower()})
    match = INTERACTIONS.get(key)

    if match:
        return json.dumps({
            "medication_a": medication_a,
            "medication_b": medication_b,
            "interaction_found": True,
            "risk_level": match["risk"],
            "effect": match["effect"],
            "advice": match["advice"],
            "disclaimer": "⚠️ Always consult your doctor or pharmacist before changing any medication.",
        })

    return json.dumps({
        "medication_a": medication_a,
        "medication_b": medication_b,
        "interaction_found": False,
        "risk_level": "LOW",
        "effect": "No known major interaction found in our database.",
        "advice": "This does not mean the combination is completely safe. Always check with your pharmacist.",
        "disclaimer": "⚠️ Always consult your doctor or pharmacist before changing any medication.",
    })


# ── Entry point (stdio transport for MCPToolset) ──────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="stdio")
