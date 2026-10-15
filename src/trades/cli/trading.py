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
    orders_path = Path(args.journal).parent / "paper_orders.csv"
    paper = pd.read_csv(orders_path, dtype=str) if orders_path.exists() else None
    report = render_report(
        journal, bars, exp.benchmark, last_completed_session(), paper, float(exp.account["slippage_bps"])
    )
    Path(args.report).write_text(report)
    print(report)
    return 0


def cmd_paper(args) -> int:
    """Plan (or with --submit, send) today's orders on the Alpaca paper account."""
    from pathlib import Path

    from trades.engine.paper import HALT_FILE, Limits, halted, run
    from trades.execution.alpaca import PaperAPIError, PaperClient
    from trades.journal.experiment import Experiment, code_version
    from trades.journal.recorder import StaleData
    from trades.settings import SettingsStore

    store = SettingsStore()
    if args.paper_command in ("halt", "resume"):
        on = args.paper_command == "halt"
        store.update({"paper_halted": on})
        if on:
            HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
            HALT_FILE.write_text(f"Paper trading halted: {args.reason or 'no reason given'}\n")
            print(f"Kill switch ON. Commit {HALT_FILE} to stop the GitHub Actions job too.")
        else:
            HALT_FILE.unlink(missing_ok=True)
            print(f"Kill switch OFF (and {HALT_FILE} removed; commit that to resume the Actions job).")
        return 0
    s = store.get()
    exp = Experiment.load(args.experiment)
    a = exp.account
    limits = Limits(
        halted=halted(s.paper_halted),
        max_daily_loss=s.paper_max_daily_loss,
        max_orders=s.paper_max_orders,
        max_gross=min(1.0, float(a["max_gross_exposure"])),
        allow_short=bool(a["allow_short"]),
        fractional=bool(a["fractional"]),
    )
    try:
        client = PaperClient(*s.alpaca_credentials())
        return run(
            exp,
            client,
            _journal_fetcher(),
            limits,
            submit=args.submit,
            orders_path=Path(args.orders),
            halt_check=lambda: halted(store.get().paper_halted),
            version=code_version(),
        )
    except (PaperAPIError, StaleData) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


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
        "paper", help="trade the experiment on an Alpaca PAPER account (dry run unless --submit; never real money)"
    )
    p.add_argument("paper_command", nargs="?", choices=("halt", "resume"), help="turn the kill switch on or off")
    p.add_argument("--submit", action="store_true", help="send the orders (default: print them only)")
    p.add_argument("--reason", default="", help="with halt: why (written to journal/PAPER_HALTED)")
    p.add_argument("--experiment", default="journal/experiment.json")
    p.add_argument("--orders", default="journal/paper_orders.csv")
    p.set_defaults(func=cmd_paper)
