"""The pipeline in the app: specs, research runs, the paper record, the gate and the kill switch.

Read-only except for the kill switch, which a person may turn on or off from the app (the API is
behind the app's password when hosted). Freezing a spec, opening a locked test, attestations and
paper orders stay on the command line, where they ask for a typed confirmation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from trades import config, runs, specs
from trades.engine.paper import PaperRecords
from trades.reporting.payloads import sanitize
from trades.risk import killswitch, load_limits

router = APIRouter()


class SwitchBody(BaseModel):
    on: bool
    reason: str = Field("", max_length=300)


def _run_brief(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    checks = run.get("checklist") or []
    return {
        "id": run["id"],
        "created": run.get("created"),
        "verdict": run.get("verdict"),
        "provider": (run.get("data") or {}).get("provider"),
        "passed": sum(1 for c in checks if c.get("status") == "pass"),
        "failed": sum(1 for c in checks if c.get("status") == "fail"),
        "pending": sum(1 for c in checks if c.get("status") == "pending"),
        "locked_test_opened": bool(((run.get("results") or {}).get("locked_test") or {}).get("opened")),
    }


def _paper_brief(key: str) -> dict[str, Any]:
    records = PaperRecords(key)
    if not records.folder.exists():
        return {"started": False}
    eq = records.equity()
    return {
        "started": True,
        "sessions": len(eq),
        "first": eq.index[0].date().isoformat() if len(eq) else None,
        "last": eq.index[-1].date().isoformat() if len(eq) else None,
        "equity": float(eq["equity"].iloc[-1]) if len(eq) else None,
        "orders": len(records.read_orders()),
        "fills": len(records.read_fills()),
    }


def _find(ref: str) -> specs.Spec:
    try:
        return specs.find(ref)
    except specs.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc


def _switch() -> dict[str, Any]:
    limits = load_limits()
    on = killswitch.is_halted(limits)
    return {"on": on, "reason": killswitch.reason(limits).strip() if on else ""}


@router.get("/pipeline")
async def pipeline():
    """Every spec with its latest research verdict and its paper record, plus the kill switch."""
    good, problems = specs.scan()
    paper_spec = config.section("paper", "paper").get("spec")
    rows = []
    for spec in good:
        rows.append({
            "id": spec.id, "key": spec.key, "name": spec.name, "version": spec.version, "status": spec.status,
            "frozen": spec.frozen.isoformat() if spec.frozen else None, "drift": spec.drift(),
            "strategy": spec.strategy.id, "timeframe": spec.timeframe, "symbols": spec.symbols[:12],
            "n_symbols": len(spec.symbols), "provider": spec.provider,
            "latest_run": _run_brief(runs.latest(spec.key)),
            "paper": _paper_brief(spec.key),
            "paper_target": paper_spec in (spec.id, spec.key),
        })
    return sanitize({"specs": rows, "problems": problems, "kill_switch": _switch(), "paper_spec": paper_spec})


@router.get("/pipeline/specs/{ref}")
async def spec_detail(ref: str):
    """A spec's rules and write-up, its research runs and the latest run's checklist and report."""
    spec = _find(ref)
    history = runs.list_runs(spec.key)
    latest = history[-1] if history else None
    report = None
    if latest is not None:
        path = runs.runs_dir() / latest["id"] / "report.md"
        report = path.read_text() if path.exists() else None
    return sanitize({
        "spec": spec.summary(),
        "body": spec.body,
        "runs": [_run_brief(r) for r in reversed(history)],
        "checklist": (latest or {}).get("checklist"),
        "report": report,
        "variants_tested": len(runs.variants_tested(spec.key)),
    })


@router.get("/pipeline/paper/{ref}")
async def paper_detail(ref: str):
    """The paper record: equity by session, recent orders, fills and events, and the latest reports."""
    spec = _find(ref)
    records = PaperRecords(spec.key)
    eq = records.equity()
    reports = sorted(records.reports_dir.glob("*.md")) if records.reports_dir.exists() else []
    tracking = records.folder / "TRACKING.md"
    gate_report = records.folder / "GATE.md"
    return sanitize({
        "key": spec.key,
        "equity": [{"date": d.date().isoformat(), "equity": float(v)} for d, v in eq["equity"].items()],
        "orders": records.read_orders()[-50:],
        "fills": records.read_fills()[-50:],
        "events": records.events()[-50:],
        "decisions": len(records.decisions()),
        "latest_report": reports[-1].read_text() if reports else None,
        "tracking": tracking.read_text() if tracking.exists() else None,
        "gate": gate_report.read_text() if gate_report.exists() else None,
    })


@router.post("/pipeline/gate/{ref}")
async def gate_check(ref: str):
    """Tracking against a replay, then the ten gate checks (fetches bars for the replay: can take a while)."""
    from trades.reporting.tracking import Tracking, TrackingError, track
    from trades.validation import gate

    spec = _find(ref)
    records = PaperRecords(spec.key)

    def compute() -> dict[str, Any]:
        try:
            result = track(spec, records)
        except TrackingError as exc:
            result = Tracking(spec.key, notes=[str(exc)])
        checks = gate.evaluate(spec, result, records)
        return {"tracking": result.to_dict(), "checks": [c.to_dict() for c in checks], "verdict": gate.verdict(checks)}

    out = await run_in_threadpool(compute)
    return sanitize(out)


@router.get("/risk/limits")
async def risk_limits():
    """risk/limits.yaml as the risk engine reads it (read-only here: the file is Jack's to edit)."""
    from dataclasses import asdict

    return {"limits": asdict(load_limits()), "file": "risk/limits.yaml"}


@router.get("/killswitch")
async def get_switch():
    return _switch()


@router.post("/killswitch")
async def set_switch(body: SwitchBody):
    """Turn the kill switch on (nothing trades) or off. Off is a person's decision: the app asks first."""
    limits = load_limits()
    if body.on:
        killswitch.halt(body.reason or "turned on in the app", limits)
    else:
        killswitch.resume(limits)
    return _switch()
