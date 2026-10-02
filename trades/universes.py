"""Symbol sets to test on, chosen so a result is not just a story about today's winners.

A watchlist picked today is full of stocks that went on to do well; any strategy tested on
it inherits that hindsight. These presets are broad, rule-based or frozen in the past. None
is fully survivorship-free (every symbol here still trades), so treat results on them as
less biased, not unbiased.
"""

from __future__ import annotations

UNIVERSES: dict[str, dict[str, object]] = {
    "sectors": {
        "label": "US sectors (the nine original sector SPDRs)",
        "symbols": ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"],
        "note": "All of the US market, split by sector since 1998: nobody picked these for having won.",
    },
    "countries": {
        "label": "Countries (iShares MSCI country ETFs)",
        "symbols": [
            "EWJ", "EWG", "EWU", "EWC", "EWA", "EWH", "EWZ", "EWW", "EWY", "EWT", "EWS", "EWQ", "EWL", "EWP",
            "EWI", "EWN", "EWD",
        ],
        "note": "Seventeen developed and emerging stock markets, most listed since 1996.",
    },
    "multi_asset": {
        "label": "Multi-asset (stocks, bonds, gold, commodities, real estate)",
        "symbols": ["SPY", "EFA", "EEM", "AGG", "TLT", "IEF", "LQD", "TIP", "GLD", "DBC", "VNQ"],
        "note": "Where the evidence for trend following is strongest: different asset classes, not one market.",
    },
    "large_caps_2010": {
        "label": "Large US companies as of January 2010 (frozen list)",
        "symbols": [
            "XOM", "MSFT", "WMT", "AAPL", "JNJ", "PG", "IBM", "JPM", "GE", "CVX", "BRK-B", "T", "WFC", "GOOGL",
            "KO", "PFE", "BAC", "ORCL", "INTC", "CSCO",
        ],
        "note": (
            "About the twenty largest US companies at the start of 2010, frozen so the list is not chosen with "
            "hindsight. All are still listed, so some survivorship bias remains."
        ),
    },
}
