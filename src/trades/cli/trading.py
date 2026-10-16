"""Trading commands: the forward journal, the paper trader and the kill switch."""

from __future__ import annotations

import sys

import pandas as pd


def _journal_fetcher():
    """fetch(provider, symbols, start) for the journal, always fresh (retries must see new bars)."""
    from trades.data.service import DataService
    from trades.settings import SettingsStore

    data = DataService(SettingsStore())

    def fetch(provider, symbols, start):
        data.invalidate()
        return data.bars_many(symbols, "1d", start, None, provider)

    return fetch


def cmd_journal_record(args) -> int:
    from pathlib import Path

    from trades.journal.experiment import Experiment
    from trades.journal.recorder import StaleData, record

    exp = Experiment.load(args.experiment)
    try:
        record(exp, _journal_fetcher(), Path(args.journal))
    except StaleData as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_journal_score(args) -> int:
    from datetime import timedelta
    from pathlib import Path

    from trades.data.calendar import last_completed_session
    from trades.journal.experiment import Experiment
    from trades.journal.scorer import render_report

    exp = Experiment.load(args.experiment)
    path = Path(args.journal)
    journal = pd.read_csv(path, dtype={"session_date": str}) if path.exists() else pd.DataFrame()
    bars: dict = {}
    if not journal.empty:
        symbols = sorted({*journal["symbol"], exp.benchmark})
        start = pd.Timestamp(journal["session_date"].min()).date() - timedelta(days=7)
        fetch = _journal_fetcher()
        problems = []
        for provider in exp.providers:
            try:
                bars, errors = fetch(provider, symbols, start)
            except Exception as exc:  # e.g. Alpaca without keys: try the next provider
                problems.append(f"{provider}: {exc}")
                continue
            if not errors:
                break
            problems.append(f"{provider}: {errors}")
        else:
            print(f"ERROR: no prices to score with: {'; '.join(problems)}", file=sys.stderr)
            return 1
    paper = None
    try:  # the paper account's fills, if config/paper.yaml's spec has traded
        from trades.engine.paper import PaperRecords
        from trades.specs import SpecError

        orders_path = PaperRecords(_paper_spec(args).key).orders_path
        paper = pd.read_csv(orders_path, dtype=str) if orders_path.exists() else None
    except SpecError:
        pass
    report = render_report(
        journal, bars, exp.benchmark, last_completed_session(), paper, float(exp.account["slippage_bps"])
    )
    Path(args.report).write_text(report)
    print(report)
    return 0


def _paper_spec(args):
    """The spec named by --spec (or the command's SPEC), else config/paper.yaml's ``paper.spec``."""
    from trades import config, specs

    ref = getattr(args, "spec", None) or config.section("paper", "paper").get("spec")
    if not ref:
        raise specs.SpecError("No spec to paper trade: pass --spec or set paper.spec in config/paper.yaml.")
    return specs.find(ref)


def _paper_client():
    """The Alpaca *paper* account, with the keys from Settings or APCA_API_KEY_ID / APCA_API_SECRET_KEY."""
    from trades.execution.alpaca import PaperClient
    from trades.settings import SettingsStore

    return PaperClient(*SettingsStore().get().alpaca_credentials())


def cmd_paper(args) -> int:
    """``trades paper [run|status|flatten|drill|halt|resume]``: the paper account for one spec."""
    action = args.paper_command or "run"
    if action == "halt":
        return cmd_halt(args)
    if action == "resume":
        return cmd_resume(args)
    actions = {"run": _paper_run, "status": _paper_status, "flatten": _paper_flatten, "drill": _paper_drill,
               "backstop": _paper_backstop}
    return actions[action](args)


def _paper_run(args) -> int:
    from dataclasses import replace
    from datetime import datetime, timedelta

    from trades.data.calendar import NY, is_trading_day, session_bounds
    from trades.engine.paper import PaperRecords, PaperTarget, paper_allowed, run
    from trades.execution.alpaca import PaperAPIError
    from trades.journal.experiment import code_version
    from trades.journal.recorder import StaleData
    from trades.risk import killswitch, load_limits

    spec = _paper_spec(args)
    target = PaperTarget.from_spec(spec)
    if args.provider:
        target = replace(target, providers=[args.provider])
    validated, why = paper_allowed(spec)
    limits = load_limits()
    records = PaperRecords(target.key)
    print(why)
    try:
        client = _paper_client()
        if not target.intraday:
            return run(target, client, _journal_fetcher(), limits, records, submit=args.submit, validated=validated,
                       halt_check=lambda: killswitch.is_halted(limits), version=code_version())
        from trades.engine.intraday import IntradayRunner, Schedule, live_bars
        from trades.reporting.alerts import Alerts

        now = datetime.now(NY)
        today = now.date()
        if not is_trading_day(today):
            print(f"{today} is not a trading day: nothing to do.")
            return 0
        saved = records.load_state()
        unfinished = saved.get("session") == today.isoformat() and saved.get("phase") != "done"
        if now >= session_bounds(today)[1] + timedelta(minutes=5) and not unfinished:
            print(f"The {today} session is over. Intraday specs run through the session on a machine that "
                  "stays up: see deploy/README.md.")
            return 0
        runner = IntradayRunner(
            target, client, live_bars(target, args.provider), limits, records, session=today,
            schedule=Schedule.from_config(), submit=args.submit, validated=validated,
            halt_check=lambda: killswitch.is_halted(limits), alert=Alerts.from_config(prefix=f"[{target.key}] "),
            version=code_version(),
        )
        return runner.run()
    except (PaperAPIError, StaleData) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _paper_status(args) -> int:
    """Everything the pre-market check needs on one screen: the spec, the switches, the account, the runner."""
    import json
    from datetime import datetime, timezone

    import pandas as pd

    from trades.engine.paper import PaperRecords, PaperTarget, paper_allowed
    from trades.execution.alpaca import PaperAPIError
    from trades.risk import killswitch, load_limits

    spec = _paper_spec(args)
    target = PaperTarget.from_spec(spec)
    limits = load_limits()
    records = PaperRecords(target.key)
    kind = "intraday" if target.intraday else "daily"
    print(f"{spec.key}: {spec.name} ({kind}, {spec.strategy.id} on {', '.join(spec.symbols[:6])}"
          f"{' ...' if len(spec.symbols) > 6 else ''})")
    print(f"  Checklist: {paper_allowed(spec)[1]}")
    halted = killswitch.is_halted(limits)
    print(f"  Kill switch: {'ON: ' + killswitch.reason(limits).strip() if halted else 'off'}")
    try:
        client = _paper_client()
        account, positions, orders = client.account(), client.positions(), client.open_orders()
        equity, last = float(account["equity"]), float(account.get("last_equity") or account["equity"])
        print(f"  Paper account: equity ${equity:,.2f} ({equity / last - 1:+.2%} since the last close), "
              f"{len(positions)} position(s), {len(orders)} open order(s)")
        for sym, pos in sorted(positions.items()):
            print(f"    {sym}: {pos['qty']:g} shares, ${pos['market_value']:,.2f}")
    except PaperAPIError as exc:
        print(f"  Paper account: not reachable ({exc})")
    beat = records.folder / "heartbeat.json"
    if beat.exists():
        b = json.loads(beat.read_text())
        age = (datetime.now(timezone.utc) - pd.Timestamp(b["time"]).to_pydatetime()).total_seconds()
        print(f"  Runner heartbeat: {b['time']} ({age:,.0f} s ago), phase {b['phase']}, last bar {b['last_bar']}")
    decisions, fills = records.decisions(), records.read_fills()
    print(f"  Records ({records.folder}): {len(decisions)} decisions, {len(records.read_orders())} orders, "
          f"{len(fills)} fills, {len(records.equity())} sessions")
    return 0


def _paper_flatten(args) -> int:
    from trades.engine.paper import flatten
    from trades.execution.alpaca import PaperAPIError
    from trades.risk import load_limits

    try:
        ok, detail = flatten(_paper_client(), load_limits(), reason=args.reason or "trades paper flatten")
    except PaperAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(detail)
    return 0 if ok else 1


def _paper_backstop(args) -> int:
    """Run by a timer shortly before the close: if the intraday runner's heartbeat has stopped while
    the account holds positions, flatten (and turn the kill switch on, so a person looks first)."""
    import json
    from datetime import datetime, timezone

    import pandas as pd

    from trades import config
    from trades.data.calendar import NY, is_trading_day, session_bounds
    from trades.engine.paper import PaperRecords, flatten
    from trades.execution.alpaca import PaperAPIError
    from trades.reporting.alerts import Alerts
    from trades.risk import load_limits

    spec = _paper_spec(args)
    now = datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    if not is_trading_day(today) or not (session_bounds(today)[0] <= now < session_bounds(today)[1]):
        print("The market is closed: nothing to do (a market order now would wait for the next open).")
        return 0
    beat = PaperRecords(spec.key).folder / "heartbeat.json"
    limit = float(config.section("paper", "paper").get("backstop_after_seconds", 120))
    age = None
    if beat.exists():
        age = (now - pd.Timestamp(json.loads(beat.read_text())["time"]).to_pydatetime()).total_seconds()
    if age is not None and age <= limit:
        print(f"The runner is alive (heartbeat {age:.0f} s ago): nothing to do.")
        return 0
    try:
        client = _paper_client()
        if not client.positions():
            print("No positions: nothing to do.")
            return 0
        why = "no heartbeat" if age is None else f"no heartbeat for {age:.0f} s"
        ok, detail = flatten(client, load_limits(), reason=f"backstop: the runner stopped ({why})")
    except PaperAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    Alerts.from_config(prefix=f"[{spec.key}] ")("error", f"Backstop: the runner stopped ({why}) with positions open. {detail}")
    print(detail)
    return 0 if ok else 1


def _paper_drill(args) -> int:
    from trades.engine.paper import PaperRecords
    from trades.execution.alpaca import PaperAPIError
    from trades.reporting.alerts import Alerts
    from trades.risk import load_limits
    from trades.validation.gate import DRILLS, run_drill

    if not args.drill:
        print(f"Which drill? One of: {', '.join((*DRILLS, 'alert'))}", file=sys.stderr)
        return 1
    spec = _paper_spec(args)
    try:
        ok, detail = run_drill(args.drill, _paper_client, PaperRecords(spec.key), load_limits(),
                               alerts=Alerts.from_config(prefix=f"[{spec.key}] "))
    except PaperAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Drill {args.drill}: {'PASSED' if ok else 'FAILED'}. {detail}")
    return 0 if ok else 1


def cmd_track(args) -> int:
    """``trades track``: the paper record against a replay of the same bars through the backtest engine."""
    from trades.engine.paper import PaperRecords
    from trades.reporting.tracking import TrackingError, render, track

    spec = _paper_spec(args)
    records = PaperRecords(spec.key)
    try:
        result = track(spec, records, provider=args.provider)
    except TrackingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    text = render(result)
    if result.first is not None:
        records.folder.mkdir(parents=True, exist_ok=True)
        (records.folder / "TRACKING.md").write_text(text)
    print(text)
    return 0


def cmd_gate(args) -> int:
    """``trades gate``: the ten checks before real money; ``trades gate attest`` records what only you can confirm."""
    from trades.cli.research import _confirm
    from trades.engine.paper import PaperRecords
    from trades.reporting.tracking import Tracking, TrackingError, track
    from trades.validation import gate

    spec = _paper_spec(args)
    records = PaperRecords(spec.key)
    if args.attest:
        statement = gate.ATTESTATIONS.get(args.attest)
        if statement is None:
            print(f"ERROR: attest one of: {', '.join(gate.ATTESTATIONS)}", file=sys.stderr)
            return 1
        if not _confirm(f"{spec.key}: \"{statement}\"", spec.id, args.yes):
            print("Nothing recorded.")
            return 1
        entry = gate.attest(records, args.attest, args.note)
        print(f"Recorded: {entry['statement']} ({entry['by']}, {entry['on']}).")
        return 0
    try:
        result = track(spec, records, provider=args.provider)
    except TrackingError as exc:
        result = Tracking(spec.key, notes=[str(exc)])
    checks = gate.evaluate(spec, result, records)
    text = gate.render(spec, checks)
    if records.folder.exists():  # a spec with a paper record keeps its latest gate report beside it
        (records.folder / "GATE.md").write_text(text)
    print(text)
    return 0


def cmd_halt(args) -> int:
    from trades.risk import killswitch

    path = killswitch.halt(args.reason)
    print(f"Kill switch ON: {path} exists, so nothing trades. Commit it to stop the scheduled GitHub job too:")
    print(f"  git add {path.name} && git commit -m 'Halt trading' && git push")
    return 0


def cmd_resume(args) -> int:
    from trades.risk import killswitch

    if killswitch.resume():
        print("Kill switch OFF: the HALT file is gone. Commit the deletion so the scheduled GitHub job resumes too.")
    else:
        print("The kill switch was already off.")
    return 0


def register(sub) -> None:
    p = sub.add_parser("halt", help="kill switch ON: create the HALT file, so nothing trades")
    p.add_argument("--reason", default="", help="why (written into the file)")
    p.set_defaults(func=cmd_halt)
    p = sub.add_parser("resume", help="kill switch OFF: remove the HALT file")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("journal", help="forward journal: record today's recommendations, score past ones")
    jsub = p.add_subparsers(dest="journal_command", required=True)
    for name, func, text in (
        ("record", cmd_journal_record, "after the close: log the Live Desk's calls on today's session"),
        ("score", cmd_journal_score, "score rows whose 5- and 21-session windows have passed"),
    ):
        jp = jsub.add_parser(name, help=text)
        jp.add_argument("--experiment", default="journal/experiment.json")
        jp.add_argument("--journal", default="journal/recommendations.csv")
        if name == "score":
            jp.add_argument("--report", default="journal/REPORT.md")
        jp.set_defaults(func=func)

    p = sub.add_parser(
        "paper",
        help="trade a spec on an Alpaca PAPER account (dry run unless --submit; never real money)",
        description="run (default): one nightly run for a daily spec, or the whole session for an intraday "
        "spec. status: the pre-market check. flatten: kill switch on, cancel everything, close everything. "
        "drill NAME: rehearse a kill switch (halt, flatten, daily-loss, revoke-key) or test the alerts (alert). "
        "backstop: flatten if the intraday runner's heartbeat stopped while positions are open (for a timer).",
    )
    p.add_argument("paper_command", nargs="?", choices=("run", "status", "flatten", "drill", "backstop", "halt", "resume"))
    p.add_argument("drill", nargs="?", help="with drill: halt | flatten | daily-loss | revoke-key | alert")
    p.add_argument("--spec", help="the spec to trade (default: paper.spec in config/paper.yaml)")
    p.add_argument("--submit", action="store_true", help="send the orders (default: print them only)")
    p.add_argument("--provider", help="override the spec's data provider")
    p.add_argument("--reason", default="", help="with halt or flatten: why (written into the HALT file)")
    p.set_defaults(func=cmd_paper)

    p = sub.add_parser("track", help="paper trading against a replay of the same bars: signals, fills, expectancy")
    p.add_argument("spec", nargs="?", help="default: paper.spec in config/paper.yaml")
    p.add_argument("--provider", help="where the replay's bars come from (default: the spec's provider)")
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("gate", help="the paper-to-live gate: ten checks before real money")
    p.add_argument("spec", nargs="?", help="default: paper.spec in config/paper.yaml")
    p.add_argument("--attest", choices=("live_config", "broker_account"), help="record what only you can confirm")
    p.add_argument("--note", default="", help="with --attest: anything worth writing down")
    p.add_argument("--provider", help="where the replay's bars come from (default: the spec's provider)")
    p.add_argument("--yes", action="store_true", help="with --attest: skip the typed confirmation")
    p.set_defaults(func=cmd_gate)
