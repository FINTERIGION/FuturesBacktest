"""SA calendar-spread roll: map trading dates to the executable CZCE contract.

Calendar (calendar months, not delivery months):
  Dec / Jan / Feb / Mar  ->  May contract (Dec uses next year's May)
  Apr / May / Jun / Jul  ->  September contract (same year)
  Aug / Sep / Oct / Nov  ->  next year's January contract

Contract codes are resolved from ``data/SA.csv`` (CZCE 3-digit YMM or
4-digit YYMM suffixes such as SA509 / SA2505), not constructed by hand.
"""

from __future__ import annotations

import re
from typing import Dict, Hashable, Iterable, Optional, Tuple

import pandas as pd

Expiry = Tuple[int, int]  # (year, month)

_CONTRACT_RE = re.compile(r'^[A-Za-z]+(\d+)$')


def normalize_contract_code(code) -> str:
    """Strip whitespace so CZCE codes match Backtrader feed names."""
    return str(code).strip().replace(' ', '')


def target_expiry(dt) -> Expiry:
    """Return the (year, month) of the contract that should be traded on ``dt``."""
    ts = pd.Timestamp(dt)
    year, month = int(ts.year), int(ts.month)
    if month == 12:
        return year + 1, 5
    if month in (1, 2, 3):
        return year, 5
    if month in (4, 5, 6, 7):
        return year, 9
    return year + 1, 1


def parse_contract_expiry(code, asof) -> Optional[Expiry]:
    """Parse a CZCE contract code into (expiry_year, expiry_month).

    4-digit suffixes are YYMM (SA2505 -> 2025-05).
    3-digit suffixes are YMM (SA509 -> year ending in 5, month 09),
    disambiguated with ``asof`` so the expiry is not already past.
    """
    code = normalize_contract_code(code)
    match = _CONTRACT_RE.match(code)
    if not match:
        return None

    digits = match.group(1)
    asof = pd.Timestamp(asof).normalize()

    if len(digits) >= 4:
        yy = int(digits[-4:-2])
        month = int(digits[-2:])
        if not 1 <= month <= 12:
            return None
        return 2000 + yy, month

    if len(digits) == 3:
        year_digit = int(digits[0])
        month = int(digits[1:])
        if not 1 <= month <= 12:
            return None
        best: Optional[pd.Timestamp] = None
        best_exp: Optional[Expiry] = None
        for year in range(int(asof.year) - 2, int(asof.year) + 12):
            if year % 10 != year_digit:
                continue
            exp = pd.Timestamp(year=year, month=month, day=1)
            month_end = exp + pd.offsets.MonthEnd(0)
            if month_end < asof:
                continue
            if best is None or exp < best:
                best = exp
                best_exp = (year, month)
        return best_exp

    return None


def infer_code_expiry(code: str, sample_dates: Iterable) -> Optional[Expiry]:
    """Infer expiry for a contract from the dates it actually traded."""
    dates = list(sample_dates)
    if not dates:
        return None
    first = min(pd.Timestamp(d) for d in dates)
    return parse_contract_expiry(code, first)


def build_date_contract_map(
    trading_index: Iterable,
    contracts_df: pd.DataFrame,
    warn: bool = True,
) -> Dict[Hashable, str]:
    """Map each trading date to the calendar contract code present in ``contracts_df``.

    ``contracts_df`` must contain ``date`` and ``contract`` columns.
    If the target contract has no row on that date, the previous mapped
    contract is kept and a warning is printed (once per target expiry).
    """
    if contracts_df.empty:
        raise ValueError("contracts_df is empty; cannot build a roll calendar")

    df = contracts_df.copy()
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    df['contract'] = df['contract'].map(normalize_contract_code)

    code_expiry: Dict[str, Expiry] = {}
    for code, grp in df.groupby('contract', sort=False):
        expiry = infer_code_expiry(code, grp['date'])
        if expiry is not None:
            code_expiry[code] = expiry

    expiry_to_codes: Dict[Expiry, list] = {}
    for code, expiry in code_expiry.items():
        expiry_to_codes.setdefault(expiry, []).append(code)

    dates_by_code = {
        code: set(pd.to_datetime(grp['date']).dt.normalize())
        for code, grp in df.groupby('contract', sort=False)
    }

    mapping: Dict[Hashable, str] = {}
    prev_code: Optional[str] = None
    warned_expiries = set()

    for raw_dt in trading_index:
        dt = pd.Timestamp(raw_dt).normalize()
        expiry = target_expiry(dt)
        candidates = expiry_to_codes.get(expiry, [])

        code = None
        for cand in candidates:
            if dt in dates_by_code.get(cand, ()):
                code = cand
                break
        if code is None and candidates:
            # Listed, but no print that session — still tradable after ffill
            # if it has any history on or before this date.
            for cand in candidates:
                earlier = {d for d in dates_by_code.get(cand, ()) if d <= dt}
                if earlier:
                    code = cand
                    break

        if code is None:
            if prev_code is not None:
                if warn and expiry not in warned_expiries:
                    print(
                        f"[RollCalendar] {dt.date()}: no contract for "
                        f"{expiry[0]}-{expiry[1]:02d}, keeping {prev_code}"
                    )
                    warned_expiries.add(expiry)
                code = prev_code
            elif warn and expiry not in warned_expiries:
                print(
                    f"[RollCalendar] {dt.date()}: no contract for "
                    f"{expiry[0]}-{expiry[1]:02d} and no previous contract"
                )
                warned_expiries.add(expiry)

        if code is not None:
            mapping[dt] = code
            prev_code = code

    return mapping


def mapping_as_dates(mapping: Dict[Hashable, str]) -> Dict:
    """Convert Timestamp keys to ``datetime.date`` for strategy params."""
    return {pd.Timestamp(k).date(): v for k, v in mapping.items()}
