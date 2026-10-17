"""Data commands: check a provider's key, and the parquet cache of downloaded bars."""

from __future__ import annotations


def cmd_data_check(args) -> int:
    from trades.data.base import DataError
    from trades.data.service import DataService
    from trades.settings import SettingsStore

    try:
        prov = DataService(SettingsStore(), disk_cache=None).provider(args.provider)
    except DataError as exc:
        print(f"{args.provider}: {exc}")
        return 1
    ok, message = prov.check()
    print(f"{prov.info.label}: {message}")
    return 0 if ok else 1


def _cache():
    from trades.data import cache as disk

    root = disk.default_root()
    if root is None:
        print("The bar cache is off (TRADES_DATA_CACHE=off, or data.cache_dir is empty in config/base.yaml).")
        return None
    if not disk.available():
        print('The bar cache needs pyarrow: pip install -e ".[cache]"')
        return None
    return disk.BarCache(root)


def cmd_data_cache(args) -> int:
    cache = _cache()
    if cache is None:
        return 0
    entries = cache.entries(args.provider)
    if not entries:
        print(f"Nothing cached yet in {cache.root}. Research on massive or alpaca data fills it.")
        return 0
    print(f"{'PROVIDER':<16}{'SYMBOL':<9}{'TF':<5}{'BARS':>10}  {'FIRST':<11} {'LAST':<11} UPDATED")
    for e in entries:
        print(
            f"{e.provider:<16}{e.symbol:<9}{e.timeframe:<5}{e.bars:>10,}  {e.first[:10]:<11} {e.last[:10]:<11} "
            f"{e.updated[:16]}"
        )
    size = sum(e.size for e in entries)
    print(f"\n{len(entries)} file(s), {size / 1e6:.1f} MB in {cache.root}")
    print("Only completed bars are stored; today's are always fetched fresh.")
    return 0


def cmd_data_clear(args) -> int:
    cache = _cache()
    if cache is None:
        return 0
    removed = cache.clear(args.provider, args.symbol)
    print(f"Removed {removed} cached file(s) from {cache.root}.")
    return 0


def register(sub) -> None:
    p = sub.add_parser("data", help="market data: check a provider, and the cache of downloaded bars")
    dsub = p.add_subparsers(dest="data_command", required=True, metavar="data_command")
    q = dsub.add_parser("check", help="one cheap request to a provider: is the key right, how fresh is it?")
    q.add_argument("provider", help="synthetic | yahoo | alpaca | csv | massive")
    q.set_defaults(func=cmd_data_check)
    q = dsub.add_parser("cache", help="what the parquet cache holds (data.cache_dir in config/base.yaml)")
    q.add_argument("--provider", help="only this provider's files, e.g. massive or alpaca-iex")
    q.set_defaults(func=cmd_data_cache)
    q = dsub.add_parser("clear", help="delete cached bars (do this after changing data plans)")
    q.add_argument("--provider", help="only this provider's files, e.g. massive or alpaca-sip")
    q.add_argument("--symbol", help="only this symbol")
    q.set_defaults(func=cmd_data_clear)
