from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "app.db"
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Evidence-Based Candidate Profiles")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


ROLE_TYPES = [
    "Product Marketing",
    "Growth Marketing",
    "Demand Generation",
    "Product Management",
    "Sales",
    "Customer Success",
    "Engineering",
    "Data Science",
]

CHANNEL_OPTIONS = [
    "Paid Search",
    "Paid Social",
    "Organic Search",
    "Email",
    "Partnerships",
    "Outbound Sales",
    "Community",
    "Events",
    "Content",
]

BUDGET_RANGES = [
    "< $10k / month",
    "$10k - $50k / month",
    "$50k - $200k / month",
    "$200k+ / month",
]

ICP_OPTIONS = ["B2B", "B2C"]


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                role_type TEXT NOT NULL,
                job_description TEXT NOT NULL,
                requirements_json TEXT NOT NULL,
                candidate_token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER NOT NULL,
                channels_json TEXT NOT NULL,
                budget_range TEXT NOT NULL,
                icp TEXT NOT NULL,
                risks TEXT NOT NULL,
                portfolio_links TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(role_id) REFERENCES roles(id)
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                action_taken TEXT NOT NULL,
                constraint_text TEXT NOT NULL,
                numeric_result TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            );
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def extract_requirements(job_description: str) -> list[str]:
    lines = [line.strip() for line in job_description.splitlines() if line.strip()]
    bullets = []
    for line in lines:
        if line.startswith(("-", "*", "•")):
            bullets.append(line.lstrip("-*• "))
    if bullets:
        return bullets
    if len(lines) > 1:
        return lines
    sentences = [sentence.strip() for sentence in job_description.replace("\n", " ").split(".")]
    return [sentence for sentence in sentences if sentence]


def compute_risk_flags(metrics: list[dict[str, Any]], risks: str) -> list[str]:
    flags = []
    if "none" in risks.lower():
        flags.append("Candidate reported no risks; validate assumptions during interview.")
    incomplete = [
        metric
        for metric in metrics
        if not metric["numeric_result"].strip() or not metric["timeframe"].strip()
    ]
    if incomplete:
        flags.append("Some metrics are missing numeric results or timeframes.")
    if len(metrics) < 3:
        flags.append("Fewer than 3 metrics were provided.")
    return flags


def suggested_interview_focus(requirements: list[str], flags: list[str]) -> list[str]:
    focus = []
    if flags:
        focus.append("Validate reported results with deeper metric walkthroughs.")
    if requirements:
        focus.append("Probe experience against top role requirements.")
    focus.append("Explore decision-making under constraints and tradeoffs.")
    return focus


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    with get_connection() as connection:
        roles = connection.execute(
            "SELECT id, title, role_type, created_at FROM roles ORDER BY created_at DESC"
        ).fetchall()
    return TEMPLATES.TemplateResponse(
        "index.html",
        {"request": request, "roles": roles},
    )


@app.get("/roles/new", response_class=HTMLResponse)
def role_form(request: Request) -> Response:
    return TEMPLATES.TemplateResponse(
        "role_form.html",
        {"request": request, "role_types": ROLE_TYPES},
    )


@app.post("/roles")
def create_role(
    title: str = Form(...),
    role_type: str = Form(...),
    job_description: str = Form(...),
) -> Response:
    if role_type not in ROLE_TYPES:
        raise HTTPException(status_code=400, detail="Unknown role type")
    requirements = extract_requirements(job_description)
    candidate_token = uuid4().hex
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO roles (title, role_type, job_description, requirements_json, candidate_token, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                role_type,
                job_description,
                json.dumps(requirements),
                candidate_token,
                datetime.utcnow().isoformat(),
            ),
        )
        role_id = cursor.lastrowid
    return RedirectResponse(url=f"/roles/{role_id}", status_code=303)


@app.get("/roles/{role_id}", response_class=HTMLResponse)
def role_detail(role_id: int, request: Request) -> Response:
    with get_connection() as connection:
        role = connection.execute(
            "SELECT * FROM roles WHERE id = ?",
            (role_id,),
        ).fetchone()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    requirements = json.loads(role["requirements_json"])
    candidate_link = request.url_for("candidate_form", token=role["candidate_token"])
    return TEMPLATES.TemplateResponse(
        "role_detail.html",
        {
            "request": request,
            "role": role,
            "requirements": requirements,
            "candidate_link": candidate_link,
        },
    )


@app.get("/apply/{token}", response_class=HTMLResponse, name="candidate_form")
def candidate_form(token: str, request: Request) -> Response:
    with get_connection() as connection:
        role = connection.execute(
            "SELECT * FROM roles WHERE candidate_token = ?",
            (token,),
        ).fetchone()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return TEMPLATES.TemplateResponse(
        "candidate_form.html",
        {
            "request": request,
            "role": role,
            "channel_options": CHANNEL_OPTIONS,
            "budget_ranges": BUDGET_RANGES,
            "icp_options": ICP_OPTIONS,
        },
    )


@app.post("/apply/{token}")
def submit_candidate(
    token: str,
    channels: list[str] = Form(...),
    budget_range: str = Form(...),
    icp: str = Form(...),
    risks: str = Form(...),
    portfolio_links: str = Form(""),
    metric_channel: list[str] = Form(...),
    metric_action: list[str] = Form(...),
    metric_constraint: list[str] = Form(...),
    metric_result: list[str] = Form(...),
    metric_timeframe: list[str] = Form(...),
) -> Response:
    with get_connection() as connection:
        role = connection.execute(
            "SELECT * FROM roles WHERE candidate_token = ?",
            (token,),
        ).fetchone()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if budget_range not in BUDGET_RANGES:
        raise HTTPException(status_code=400, detail="Unknown budget range")
    if icp not in ICP_OPTIONS:
        raise HTTPException(status_code=400, detail="Unknown ICP")
    if not risks.strip():
        raise HTTPException(status_code=400, detail="Risks are required")

    metrics: list[dict[str, str]] = []
    for index in range(len(metric_channel)):
        channel = metric_channel[index].strip()
        action = metric_action[index].strip()
        constraint = metric_constraint[index].strip()
        result = metric_result[index].strip()
        timeframe = metric_timeframe[index].strip()
        if not any([channel, action, constraint, result, timeframe]):
            continue
        metrics.append(
            {
                "channel": channel or "(not specified)",
                "action_taken": action or "(not specified)",
                "constraint": constraint or "(not specified)",
                "numeric_result": result or "(not specified)",
                "timeframe": timeframe or "(not specified)",
            }
        )

    if len(metrics) < 3:
        raise HTTPException(status_code=400, detail="Provide at least 3 metrics")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO candidates (role_id, channels_json, budget_range, icp, risks, portfolio_links, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                role["id"],
                json.dumps(channels),
                budget_range,
                icp,
                risks,
                portfolio_links,
                datetime.utcnow().isoformat(),
            ),
        )
        candidate_id = cursor.lastrowid
        connection.executemany(
            """
            INSERT INTO metrics (candidate_id, channel, action_taken, constraint_text, numeric_result, timeframe)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    candidate_id,
                    metric["channel"],
                    metric["action_taken"],
                    metric["constraint"],
                    metric["numeric_result"],
                    metric["timeframe"],
                )
                for metric in metrics
            ],
        )
    return RedirectResponse(url=f"/report/{candidate_id}", status_code=303)


@app.get("/report/{candidate_id}", response_class=HTMLResponse)
def report(candidate_id: int, request: Request) -> Response:
    report_payload = build_report_payload(candidate_id)
    return TEMPLATES.TemplateResponse(
        "report.html",
        {"request": request, **report_payload},
    )


@app.get("/report/{candidate_id}/pdf")
def report_pdf(candidate_id: int) -> Response:
    report_payload = build_report_payload(candidate_id)
    html = TEMPLATES.get_template("report.html").render(**report_payload)
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="PDF generation requires weasyprint to be installed.",
        ) from exc
    pdf_bytes = HTML(string=html, base_url=str(BASE_DIR)).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=report.pdf",
        },
    )


def build_report_payload(candidate_id: int) -> dict[str, Any]:
    with get_connection() as connection:
        candidate = connection.execute(
            "SELECT * FROM candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        role = connection.execute(
            "SELECT * FROM roles WHERE id = ?",
            (candidate["role_id"],),
        ).fetchone()
        metrics_rows = connection.execute(
            "SELECT * FROM metrics WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchall()
    requirements = json.loads(role["requirements_json"])
    metrics = [
        {
            "channel": row["channel"],
            "action_taken": row["action_taken"],
            "constraint": row["constraint_text"],
            "numeric_result": row["numeric_result"],
            "timeframe": row["timeframe"],
        }
        for row in metrics_rows
    ]
    flags = compute_risk_flags(metrics, candidate["risks"])
    focus = suggested_interview_focus(requirements, flags)
    return {
        "role": role,
        "candidate": candidate,
        "requirements": requirements,
        "channels": json.loads(candidate["channels_json"]),
        "metrics": metrics,
        "risk_flags": flags,
        "interview_focus": focus,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }
