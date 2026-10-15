"""No look-ahead: a strategy's past must not change when the future arrives.

For every registered strategy, signals, diagnostics and sized weights computed on data cut off
at bar ``t`` must equal those computed on the full history, up to ``t``. A rule that peeks at
later bars fails here. This is the property that makes every backtest in this repository honest,
so it runs as its own test tier: ``pytest tests/lookahead``.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.helpers import strategy_inputs, strategy_params
from trades.strategies import REGISTRY
from trades.strategies.sizing import SizingConfig, apply_sizing

LEVELS = ("stop_losses", "take_profits", "entry_stops")


def _sizing_inputs(output):
    return {"stop_losses": output.stop_losses, "entry_stops": output.entry_stops}


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_no_look_ahead(sid, daily, intraday):
    """Signals, order levels, diagnostics and sized weights at bar t must not change when later
    bars are added. Intraday strategies are cut at a session boundary and in mid-session."""
    cls = REGISTRY[sid]
    strat = cls(**strategy_params(cls))
    if cls.intraday:
        data = strategy_inputs(cls, daily, intraday)
        cuts = (3900, 6041, 9066)  # 3900 = the start of the 51st session; the others fall mid-session
    else:
        data = {s: df.iloc[-900:] for s, df in strategy_inputs(cls, daily).items()}
        cuts = (420, 611, 837)
    full = strat.run(data)
    sizing = SizingConfig.from_dict(None, cls.default_sizing)
    w_full = apply_sizing(full.signals, data, sizing, cls.kind, **_sizing_inputs(full))
    for cut in cuts:
        part_data = {s: df.iloc[:cut] for s, df in data.items()}
        part = strat.run(part_data)
        np.testing.assert_allclose(
            part.signals.to_numpy(), full.signals.iloc[:cut].to_numpy(), equal_nan=True
        )
        for name in LEVELS:
            a, b = getattr(full, name), getattr(part, name)
            assert (a is None) == (b is None), name
            if a is not None:
                np.testing.assert_allclose(b.to_numpy(), a.iloc[:cut].to_numpy(), equal_nan=True, err_msg=name)
        for sym in data:
            a = full.diagnostics[sym].iloc[:cut].select_dtypes(["number", "bool"]).astype(float)
            b = part.diagnostics[sym].select_dtypes(["number", "bool"]).astype(float)
            np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), equal_nan=True, err_msg=f"{sid}/{sym}")
        w_part = apply_sizing(part.signals, part_data, sizing, cls.kind, **_sizing_inputs(part))
        np.testing.assert_allclose(w_part.to_numpy(), w_full.iloc[:cut].to_numpy(), atol=1e-12)
