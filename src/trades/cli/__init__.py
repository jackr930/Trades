"""The ``trades`` command line. Commands follow the playbook's pipeline:

  1. write it down     trades spec new | show | list | version | freeze
  2. research it       trades research STRAT-xxx            (writes runs/<id>/)
                       trades runs list | show | reproduce
  3. paper trade it    trades paper                         (Alpaca paper only)
                       trades halt | resume                 (the kill switch)
  4. watch it          trades journal record | score
  the app              trades serve | strategies | backtest | recommend
"""

from __future__ import annotations

import argparse
import os
import sys

from trades.cli import app, research, trading


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trades", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    research.register(sub)
    trading.register(sub)
    app.register(sub)
    args = parser.parse_args(argv)
    from trades.config import ConfigError
    from trades.specs import SpecError
    from trades.validation.research import ResearchError

    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:  # e.g. `trades strategies | head`: the reader left; stop quietly
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0
    except (SpecError, ResearchError, ConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


__all__ = ["main"]
