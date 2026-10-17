"""Research commands: strategy specs, research runs and the record they leave.

  trades spec list                       every spec, its status and its latest verdict
  trades spec show STRAT-002             the spec's rules and its latest checklist
  trades spec new STRAT-004 my-idea      a new draft from specs/TEMPLATE.md
  trades spec version STRAT-002          a new draft version of a spec (the old one stays)
  trades spec freeze STRAT-002           fix the rules (Jack only)
  trades research STRAT-002              run the acceptance checklist; writes runs/<id>/
  trades research STRAT-002 --open-locked-test   open the locked test, once (Jack only)
  trades runs list [STRAT-002]           past runs
  trades runs show RUN_ID                a run's report
  trades runs reproduce RUN_ID           rerun it and compare the results
"""

from __future__ import annotations

import sys
from datetime import date


def _confirm(prompt: str, expected: str, assume_yes: bool) -> bool:
    """Irreversible steps ask a person to type ``expected``; ``--yes`` skips this (scripts)."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{prompt}\nRefusing without a terminal: run it yourself, or pass --yes.", file=sys.stderr)
        return False
    answer = input(f"{prompt}\nType {expected} to continue: ").strip()
    return answer.upper() == expected.upper()


def _print_checklist(checklist: list[dict]) -> None:
    mark = {"pass": "PASS", "fail": "FAIL", "pending": "....", "n/a": " n/a"}
    for c in checklist:
        print(f"  {c['n']:>2}. [{mark.get(c['status'], c['status'])}] {c['name']}: {c['value']} (needs {c['threshold']})")


def cmd_spec_list(args) -> int:
    from trades import runs, specs

    good, problems = specs.scan()
    print(f"{'Spec':16s} {'Status':12s} {'Frozen':11s} {'Last run':17s} {'Verdict':22s} Name")
    for s in good:
        last = runs.latest(s.key)
        when = last["created"][:16].replace("T", " ") if last else "-"
        verdict = last["verdict"] if last else "-"
        drift = " (CHANGED AFTER FREEZING)" if s.drift() else ""
        print(f"{s.key:16s} {s.status:12s} {str(s.frozen or '-'):11s} {when:17s} {verdict:22s} {s.name}{drift}")
    for name, problem in problems.items():
        print(f"{name}: cannot be read: {problem}", file=sys.stderr)
    return 1 if problems else 0


def cmd_spec_show(args) -> int:
    from trades import runs, specs

    s = specs.find(args.spec)
    print(f"{s.key}: {s.name}\n  file: {s.path}\n  status: {s.status}; frozen: {s.frozen or 'no (draft)'}")
    if s.drift():
        print(f"  WARNING: {s.drift()}")
    print(f"  strategy: {s.strategy.id} {s.strategy.params or ''}; sizing: {s.strategy.sizing or 'default'}")
    print(f"  universe: {len(s.symbols)} symbol(s); survivorship-free: {s.universe.get('survivorship_free', False)}")
    print(f"  data: {s.provider} {s.timeframe}")
    for name, p in s.periods.items():
        print(f"  {name}: {p.start} to {p.end}{' (locked)' if name == 'test' else ''}")
    opened = runs.locked_test_run(s.key)
    print(f"  locked test: {'opened on ' + opened['locked_test']['opened_on'] if opened else 'sealed'}")
    print(f"  variants tested: {len(runs.variants_tested(s.key))}")
    last = runs.latest(s.key)
    if last:
        print(f"\nLatest run {last['id']}: {last['verdict']}")
        _print_checklist(last["checklist"])
    else:
        print(f"\nNo runs yet: `trades research {s.id}`.")
    return 0


def cmd_spec_new(args) -> int:
    import re

    from trades import specs

    sid = args.id.upper()
    if not specs.ID_PATTERN.match(sid):
        print("ERROR: a spec id looks like STRAT-004", file=sys.stderr)
        return 1
    if list(specs.spec_dir().glob(f"{sid}*.md")):
        print(f"ERROR: {sid} already exists; use `trades spec version {sid}` for a new version", file=sys.stderr)
        return 1
    slug = re.sub(r"[^a-z0-9]+", "-", args.name.lower()).strip("-") or "idea"
    text = (specs.spec_dir() / "TEMPLATE.md").read_text()
    text = re.sub(r"(?m)^# Copy to .*\n(#.*\n)*", "", text, count=1)
    text = text.replace("id: STRAT-NNN", f"id: {sid}").replace("STRAT-NNN", sid)
    path = specs.spec_dir() / f"{sid}-{slug}.md"
    path.write_text(text)
    print(f"Wrote {path}: write the rules down before any code (the playbook's first step).")
    return 0


def cmd_spec_version(args) -> int:
    from trades import specs

    path = specs.new_version(specs.find(args.spec))
    print(f"Wrote {path}: a draft. Its runs, variant count and locked test start from zero.")
    return 0


def cmd_spec_freeze(args) -> int:
    from trades import specs

    s = specs.find(args.spec)
    prompt = (f"Freeze {s.key} ({s.name})? Its rules, parameter grid, costs and date splits become fixed;\n"
              "changing them later means a new version, and its runs count from zero.")
    if not _confirm(prompt, s.id, args.yes):
        print("Not frozen.")
        return 1
    frozen = specs.freeze(s, date.today())
    print(f"{frozen.key} frozen on {frozen.frozen} (rules hash {frozen.rules_hash}). Commit the file now:")
    print(f"  git add {frozen.path} && git commit -m 'Freeze {frozen.key}'")
    return 0


def cmd_research(args) -> int:
    from trades import specs
    from trades.validation.research import research

    if args.open_locked_test:
        s = specs.find(args.spec)
        prompt = (f"Open {s.key}'s locked test ({s.periods['test'].start} to {s.periods['test'].end})?\n"
                  "This happens once per spec version. If it fails, the strategy is rejected, not retuned.")
        if not _confirm(prompt, s.id, args.yes):
            print("The locked test stays sealed.")
            return 1
    record = research(args.spec, open_locked_test=args.open_locked_test, provider=args.provider)
    print()
    _print_checklist(record["checklist"])
    print(f"\nReport: runs/{record['id']}/report.md")
    return 0 if record["verdict"] in ("pass", "ready_for_locked_test") else 2


def cmd_runs_list(args) -> int:
    from trades import runs

    key = None
    if args.spec:
        from trades import specs

        key = specs.find(args.spec).key
    rows = runs.list_runs(key)
    if not rows:
        print("No runs yet.")
        return 0
    for r in rows:
        lt = " locked test opened" if r.get("locked_test", {}).get("opened") else ""
        print(f"{r['id']:40s} {r['verdict']:22s} variants {r.get('variants_tested', '?')}{lt}")
    return 0


def cmd_runs_show(args) -> int:
    from trades import runs

    print((runs.runs_dir() / args.run_id / "report.md").read_text())
    return 0


def cmd_runs_reproduce(args) -> int:
    from trades import runs
    from trades.validation.research import research

    old = runs.load_run(args.run_id)
    git = runs.git_state()
    if old["git"].get("code_tree") and old["git"]["code_tree"] != git["code_tree"]:
        print(
            f"The run used code tree {old['git']['code_tree']}; this checkout has {git['code_tree']}.\n"
            f"Check out its commit first:  git checkout {old['git']['commit']}  (and back with: git checkout -)",
            file=sys.stderr,
        )
        return 1
    new = research(old["spec"]["key"], provider=old["data"]["provider"], write=False, log=lambda m: None)
    problems = []
    if new["data"]["hash"] != old["data"]["hash"]:
        problems.append(f"the data changed (fingerprint {old['data']['hash']} -> {new['data']['hash']}): the provider revised its bars")
    for a, b in zip(old["checklist"], new["checklist"], strict=True):
        if a["n"] == 7:
            continue  # the deflated Sharpe counts every variant tested since, so it may legitimately move
        if (a["status"], a["value"]) != (b["status"], b["value"]):
            problems.append(f"check {a['n']} ({a['name']}): {a['status']} {a['value']} -> {b['status']} {b['value']}")
    if problems:
        print("NOT reproduced:\n  " + "\n  ".join(problems))
        return 1
    print(f"Reproduced {args.run_id}: same data fingerprint, same checklist (check 7 counts later variants too).")
    return 0


def register(sub) -> None:
    p = sub.add_parser("spec", help="strategy specs: list, show, new, version, freeze")
    ssub = p.add_subparsers(dest="spec_command", required=True)
    q = ssub.add_parser("list", help="every spec, its status and its latest verdict")
    q.set_defaults(func=cmd_spec_list)
    q = ssub.add_parser("show", help="a spec's rules and its latest checklist")
    q.add_argument("spec")
    q.set_defaults(func=cmd_spec_show)
    q = ssub.add_parser("new", help="a new draft spec from specs/TEMPLATE.md")
    q.add_argument("id", help="e.g. STRAT-004")
    q.add_argument("name", help="a short name for the file, e.g. vwap-reversion")
    q.set_defaults(func=cmd_spec_new)
    q = ssub.add_parser("version", help="copy a spec to its next version, as a draft")
    q.add_argument("spec")
    q.set_defaults(func=cmd_spec_version)
    q = ssub.add_parser("freeze", help="fix a draft's rules (Jack only)")
    q.add_argument("spec")
    q.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    q.set_defaults(func=cmd_spec_freeze)

    p = sub.add_parser("research", help="run a spec through the acceptance checklist (writes runs/<id>/)")
    p.add_argument("spec")
    p.add_argument("--provider", help="override the spec's data provider (e.g. synthetic, to rehearse the pipeline)")
    p.add_argument("--open-locked-test", action="store_true", help="open the locked test, once (Jack only)")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("runs", help="research runs: list, show, reproduce")
    rsub = p.add_subparsers(dest="runs_command", required=True)
    q = rsub.add_parser("list", help="past runs, optionally for one spec")
    q.add_argument("spec", nargs="?")
    q.set_defaults(func=cmd_runs_list)
    q = rsub.add_parser("show", help="a run's report")
    q.add_argument("run_id")
    q.set_defaults(func=cmd_runs_show)
    q = rsub.add_parser("reproduce", help="rerun a run and compare its results")
    q.add_argument("run_id")
    q.set_defaults(func=cmd_runs_reproduce)
