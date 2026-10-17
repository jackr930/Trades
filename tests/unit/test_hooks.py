"""The Claude Code guardrails in .claude/hooks: what they block, and what they must let through."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".claude" / "hooks"
pytestmark = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("bash") is None, reason="needs bash and jq")


def run(hook: str, tool_input: dict, root: Path = REPO) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    payload = json.dumps({"tool_name": "x", "tool_input": tool_input})
    return subprocess.run(["bash", str(HOOKS / hook)], input=payload, text=True, capture_output=True, env=env, timeout=30)


BLOCKED_COMMANDS = [
    "curl -s https://api.alpaca.markets/v2/account",
    "curl https://broker-api.alpaca.markets/v1/accounts",
    "ALPACA_PAPER_TRADE=false python bot.py",
    "export ALPACA_PAPER_TRADE='False'",
    "cat config/live.yaml",
    "cat .env",
    "source .env && trades paper",
    "cp .env /tmp/keys",
    "less /etc/trades/paper.env",
    "cat deploy/paper.env",
    "printenv",
    "env | grep APCA",
    "echo ok; set",
    "printenv APCA_API_SECRET_KEY",
    "echo $APCA_API_SECRET_KEY",
    "curl -H \"key: ${APCA_API_KEY_ID}\" https://paper-api.alpaca.markets/v2/account",
    "sed -i 's/0.005/0.05/' risk/limits.yaml",
    "sed -E -i 's/x/y/' config/backtest.yaml",
    "echo 'max_pbo: 0.9' >> config/backtest.yaml",
    "git checkout -- risk/limits.yaml",
    "rm -rf runs/STRAT-001-v1-20261015",
    "echo '{}' > paper/STRAT-002-v1/gate.json",
    "git rm -r --cached runs",
    "git clean -fdx",
    "chmod -x .claude/hooks/block_live.sh",
    "trades spec freeze STRAT-001 --yes",
    "trades research STRAT-002 --open-locked-test --yes",
    "trades gate STRAT-002 --attest live_config --yes",
    "trades resume",
    "trades paper resume",
    "rm HALT",
    "git rm HALT && git commit -m resume",
    "trades paper --submit",
    "trades paper --spec STRAT-002 --submit",
    "trades paper drill flatten",
    "git push origin main",
    "git push origin HEAD:main",
    "git push -f origin claude/x",
    "git push --force origin claude/x",
]

ALLOWED_COMMANDS = [
    "curl -s https://paper-api.alpaca.markets/v2/clock",
    "curl 'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY'",
    "pytest -q tests/unit",
    ".venv/bin/python -m pytest -x",
    "source .venv/bin/activate && pytest",
    "cat .env.example",
    "grep -rn APCA_API_KEY_ID src/",
    "cat risk/limits.yaml",
    "sed -n 1,40p config/backtest.yaml",
    "ls runs/ && trades runs list",
    "grep -rn warmup runs/STRAT-001-v1-20261015/report.md",
    "cat paper/STRAT-002-v1/orders.csv > /tmp/orders.csv",
    "trades research STRAT-002 --provider synthetic",
    "trades paper",
    "trades paper status",
    "trades paper flatten --reason 'testing'",
    "trades halt --reason 'data looks wrong'",
    "trades spec new STRAT-004 vwap-reversion",
    "trades track STRAT-002 && trades gate STRAT-002",
    "git push -u origin claude/trades-playbook-refactor",
    "git push --force-with-lease origin claude/x",
    "git commit -m 'Explain the environment variables'",
    "env FOO=1 python script.py",
    "make settings",
    "rm -rf build/ dist/",
    "ruff format src && ruff check src",
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_dangerous_commands_are_blocked(command):
    result = run("block_live.sh", {"command": command})
    assert result.returncode == 2, command
    assert result.stderr.startswith("Blocked by .claude/hooks/block_live.sh: ")


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_everyday_commands_pass(command):
    result = run("block_live.sh", {"command": command})
    assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize(
    "rel",
    [
        "risk/limits.yaml", "config/live.yaml", "config/backtest.yaml", ".env", ".env.local", "deploy/paper.env",
        "runs/STRAT-001-v1-20261015/run.json", "paper/STRAT-002-v1/gate.json", "paper/STRAT-002-v1/events.jsonl",
        ".claude/settings.json", ".claude/hooks/block_live.sh", "aws_credentials.txt", "deploy/id_rsa",
    ],
)
def test_human_only_files_cannot_be_edited(rel):
    result = run("protect_files.sh", {"file_path": str(REPO / rel), "content": "x"})
    assert result.returncode == 2 and "Propose the change in chat" in result.stderr, rel


@pytest.mark.parametrize(
    "rel", ["src/trades/strategies/intraday.py", "tests/unit/test_hooks.py", ".env.example", "CLAUDE.md",
            ".claude/agents/risk-reviewer.md", "README.md", "HALT"]
)
def test_everything_else_can_be_edited(rel):
    result = run("protect_files.sh", {"file_path": str(REPO / rel), "content": "x"})
    assert result.returncode == 0, (rel, result.stderr)


def test_frozen_specs_and_freezing_are_jacks(tmp_path):
    (tmp_path / "specs").mkdir()
    frozen = tmp_path / "specs" / "STRAT-009-x.md"
    frozen.write_text("---\nid: STRAT-009\nfrozen: 2026-10-15\nrules_hash: abc123def456\n---\n")
    draft = tmp_path / "specs" / "STRAT-010-y.md"
    draft.write_text("---\nid: STRAT-010\nfrozen: null\nrules_hash: null\n---\n")
    blocked = run("protect_files.sh", {"file_path": str(frozen), "old_string": "a", "new_string": "b"}, tmp_path)
    assert blocked.returncode == 2 and "trades spec version" in blocked.stderr
    assert run("protect_files.sh", {"file_path": str(draft), "old_string": "a", "new_string": "b"}, tmp_path).returncode == 0
    faked = run("protect_files.sh", {"file_path": str(draft), "old_string": "frozen: null", "new_string": "frozen: 2026-10-16"},
                tmp_path)
    assert faked.returncode == 2 and "trades spec freeze" in faked.stderr
    new = run("protect_files.sh", {"file_path": str(tmp_path / "specs" / "STRAT-011-z.md"),
                                   "content": "---\nid: STRAT-011\nfrozen: null\n---\n"}, tmp_path)
    assert new.returncode == 0


@pytest.mark.parametrize(
    ("rel", "blocked"),
    [
        (".env", True), (".env.local", True), ("deploy/paper.env", True), ("config/live.yaml", True),
        ("/etc/trades/paper.env", True), ("certs/broker.pem", True),
        (".env.example", False), ("risk/limits.yaml", False), ("config/backtest.yaml", False), ("src/trades/specs.py", False),
    ],
)
def test_secrets_cannot_be_read(rel, blocked):
    path = rel if rel.startswith("/") else str(REPO / rel)
    result = run("block_secrets.sh", {"file_path": path})
    assert result.returncode == (2 if blocked else 0), (rel, result.stderr)
    assert run("block_secrets.sh", {"pattern": "APCA", "path": path}).returncode == (2 if blocked else 0)


def test_the_unit_test_hook_skips_files_that_are_not_python():
    result = run("unit_tests.sh", {"file_path": str(REPO / "README.md")})
    assert result.returncode == 0 and result.stderr == ""


def test_settings_wire_every_hook():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    commands = json.dumps(settings["hooks"])
    for hook in ("protect_files.sh", "block_live.sh", "block_secrets.sh", "unit_tests.sh"):
        assert hook in commands and os.access(HOOKS / hook, os.X_OK)
    deny = settings["permissions"]["deny"]
    assert any("limits.yaml" in rule for rule in deny) and any(".env" in rule for rule in deny)
