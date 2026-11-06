"""The cap table maths (D-FUND-4 b): a pure, deterministic engine. No database, no AI.

Conventions (each is tested):

- Money is integer minor units; shares are integers. Everything in between is `Decimal` at 60
  significant digits (`CTX`, ROUND_HALF_EVEN), never float. Values within 1e-9 of a whole number
  count as that number (see `SNAP`), so solver noise never costs a share.
- Rounding to whole numbers happens in one place each: SAFE and note shares ROUND DOWN (the holder
  never gets more than the terms give), new-investor shares ROUND DOWN, the option-pool top-up
  ROUNDS UP (the pool is "at least" the target), accrued note interest rounds half-even to a minor
  unit, vested shares round down.
- Ownership percentages are basis points (1/100 of a percent) shared out by the largest-remainder
  method so a column always sums to exactly 10 000.
- A SAFE or note has no shares until it converts. It is listed as an amount; a round converts it.

Round maths (pre-money includes the SAFEs and the pool top-up, the usual term-sheet convention):

    A      = S0 + X + sigma             pre-money capitalisation after the pool top-up and SAFEs
    P      = V / A                      the round price
    I      = N / P                      new-investor shares        (post-money is V + N)
    X      = pool top-up so that unallocated pool = target of the post-round fully-diluted total

S0 is the fully-diluted share count before the round (issued + granted options + unallocated pool,
SAFEs excluded). Each SAFE converts at the LOWEST of its cap price, its discount price and the round price P (the
most shares for the holder; with neither a cap nor a discount it converts at P):

    post-money cap price = cap / (S0 + sigma)     "company capitalisation" excludes the new pool X
    pre-money cap price  = cap / (S0 + X)         includes the pool created in this financing, not sigma
    discount price       = P * (1 - discount)

sigma depends on P and P on sigma, so it is solved by Newton's method on the convex, piecewise-linear
conversion function (a few steps, exact to 1e-20). If the converting instruments would own the whole
company there is no solution and the round is refused; a post-money SAFE set that owns 100% or more
is refused up front.

MFN: an MFN instrument converts on its own terms or on the terms of any one non-MFN instrument
issued on or after its own issue date (both dates must be known), whichever gives the holder the
most shares. See `best_terms` for why this is the tightest cap and widest discount among them.
"""

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

CTX = Context(prec=60, rounding=ROUND_HALF_EVEN)
ZERO = Decimal(0)
BPS = Decimal(10_000)
MAX_ITERATIONS = 200
CONVERGED = Decimal("1e-20")
MAX_ROUNDS = 5

# Holder kinds and securities the engine understands.
SHARE_SECURITIES = ("common", "preferred", "option")
CONVERTIBLES = ("safe", "note")


class EngineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Position:
    """Shares held by one holder in one security."""

    key: str  # holder id (or a synthetic key for the new investors)
    name: str
    kind: str  # founder | employee | advisor | investor | pool
    security: str
    shares: int


@dataclass(frozen=True)
class Convertible:
    key: str  # the entry id; reported as `ref` in conversions
    name: str
    security: str  # safe | note
    principal_minor: int
    interest_bps: int = 0  # notes only, simple interest, actual/365
    issued_on: date | None = None
    cap_minor: int | None = None
    discount_bps: int | None = None
    safe_kind: str = "post_money"  # post_money | pre_money
    mfn: bool = False
    holder_key: str = ""  # the holder the converted shares belong to (defaults to `key`)
    holder_kind: str = "investor"


@dataclass(frozen=True)
class RoundInput:
    name: str
    new_money_minor: int
    pre_money_minor: int
    pool_target_bps: int = 0  # unallocated pool as a share of the post-round fully-diluted total


@dataclass
class StepResult:
    name: str
    pre_money_minor: int
    new_money_minor: int
    post_money_minor: int
    price_per_share: str  # minor units per share, 6 decimal places
    pre_round_shares: int
    pool_top_up_shares: int
    new_investor_shares: int
    post_round_shares: int
    conversions: list[dict[str, Any]] = field(default_factory=list)
    founders_before_bps: int = 0
    founders_after_bps: int = 0


def d(value: int | str | Decimal) -> Decimal:
    return Decimal(value)


# The SAFE conversion is solved iteratively and stops within CONVERGED of the exact answer, so a
# value that is mathematically a whole number can arrive as 999 999.99999999...; rounding down
# would then lose a share. Values within SNAP of a whole number are treated as that number.
SNAP = Decimal("1e-9")


def floor_int(value: Decimal) -> int:
    return int((value + SNAP).to_integral_value(rounding=ROUND_FLOOR))


def ceil_int(value: Decimal) -> int:
    return int((value - SNAP).to_integral_value(rounding=ROUND_CEILING))


def money_str(value: Decimal, places: int = 6) -> str:
    return str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


# ---------------------------------------------------------------- percentages


def largest_remainder_bps(weights: dict[str, int]) -> dict[str, int]:
    """Basis points per key from integer weights, summing to exactly 10 000 (or 0 if all zero)."""
    total = sum(weights.values())
    if total <= 0:
        return dict.fromkeys(weights, 0)
    exact = {k: Decimal(v) * BPS / Decimal(total) for k, v in weights.items()}
    floors = {k: floor_int(v) for k, v in exact.items()}
    left = 10_000 - sum(floors.values())
    # Biggest fractional parts first; ties go to the larger holding, then the key (deterministic).
    order = sorted(exact, key=lambda k: (-(exact[k] - floors[k]), -weights[k], k))
    for k in order[:left]:
        floors[k] += 1
    return floors


def holder_shares(positions: list[Position], *, fully_diluted: bool) -> dict[str, int]:
    """Shares per holder. Basic counts issued stock only; fully diluted adds granted options and
    the unallocated pool."""
    out: dict[str, int] = {}
    for p in positions:
        if not fully_diluted and p.security == "option":
            continue
        out[p.key] = out.get(p.key, 0) + p.shares
    return out


# ---------------------------------------------------------------- vesting


def vested_shares(
    shares: int, *, start: date | None, cliff_months: int, months: int, as_of: date
) -> int:
    """Whole elapsed months since `start`; nothing before the cliff; then linear to `months`;
    rounded down. No vesting schedule means fully vested."""
    if start is None or months <= 0:
        return shares
    elapsed = (as_of.year - start.year) * 12 + (as_of.month - start.month)
    if as_of.day < start.day:
        elapsed -= 1
    if elapsed < 0 or elapsed < cliff_months:
        return 0
    return floor_int(d(shares) * d(min(elapsed, months)) / d(months))


# ---------------------------------------------------------------- convertibles


def converting_amount(c: Convertible, as_of: date) -> int:
    """Principal plus simple interest (actual/365) to `as_of`, half-even to a minor unit."""
    if c.security != "note" or c.interest_bps <= 0 or c.issued_on is None:
        return c.principal_minor
    days = max(0, (as_of - c.issued_on).days)
    interest = d(c.principal_minor) * d(c.interest_bps) * d(days) / (BPS * d(365))
    return c.principal_minor + int(interest.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class BestTerms:
    """What an instrument can convert on: its tightest cap per cap type and its biggest discount."""

    post_cap: int | None = None
    pre_cap: int | None = None
    discount_bps: int | None = None


def _tighter(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else min(a, b)


def _wider(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else max(a, b)


def _own_terms(c: Convertible) -> BestTerms:
    return BestTerms(
        post_cap=c.cap_minor if c.safe_kind == "post_money" else None,
        pre_cap=c.cap_minor if c.safe_kind == "pre_money" else None,
        discount_bps=c.discount_bps,
    )


def best_terms(convs: list[Convertible]) -> dict[str, BestTerms]:
    """The terms each instrument converts on. Its own, except an MFN instrument, which may instead
    adopt the terms of any one non-MFN instrument issued on or after its own issue date (both dates
    required). The conversion price is the LOWEST of the cap prices and the discount price, so
    taking the best single instrument's whole term set and taking the tightest cap and widest
    discount across the eligible instruments give the same price: the holder never gets more than
    one instrument's terms could give. That equivalence lets this be O(n log n) (sort the donors by
    date, keep suffix aggregates), not O(MFN x donors) work repeated in every solver step."""
    donors = sorted(
        (o for o in convs if not o.mfn and o.issued_on is not None), key=lambda o: o.issued_on  # type: ignore[arg-type,return-value]
    )
    dates = [o.issued_on for o in donors]
    suffix: list[BestTerms] = [BestTerms()] * (len(donors) + 1)
    for i in range(len(donors) - 1, -1, -1):
        t, nxt = _own_terms(donors[i]), suffix[i + 1]
        suffix[i] = BestTerms(
            _tighter(t.post_cap, nxt.post_cap),
            _tighter(t.pre_cap, nxt.pre_cap),
            _wider(t.discount_bps, nxt.discount_bps),
        )
    out: dict[str, BestTerms] = {}
    for c in convs:
        own = _own_terms(c)
        if c.mfn and c.issued_on is not None:
            later = suffix[bisect_left(dates, c.issued_on)]  # type: ignore[type-var]
            own = BestTerms(
                _tighter(own.post_cap, later.post_cap),
                _tighter(own.pre_cap, later.pre_cap),
                _wider(own.discount_bps, later.discount_bps),
            )
        out[c.key] = own
    return out


# ---------------------------------------------------------------- one round


def _check(round_: RoundInput) -> None:
    if round_.pre_money_minor <= 0 or round_.new_money_minor <= 0:
        raise EngineError("INVALID_ROUND", "Pre-money and new money must both be above zero.")
    if not 0 <= round_.pool_target_bps < 10_000:
        raise EngineError("INVALID_ROUND", "The option pool target must be between 0% and 100%.")


def apply_round(
    positions: list[Position],
    convertibles: list[Convertible],
    round_: RoundInput,
    *,
    as_of: date,
    index: int = 1,
) -> tuple[list[Position], StepResult]:
    """Convert every SAFE and note, top the pool up, issue the new shares. Returns the share
    positions after the round (convertibles are consumed) and the step's numbers."""
    with localcontext(CTX):  # every Decimal operation below runs at 60 digits, half-even
        return _apply_round(positions, convertibles, round_, as_of, index)


def _solve(
    convs: list[Convertible],
    amounts: dict[str, Decimal],
    s0d: Decimal,
    u0: int,
    v: Decimal,
    m: Decimal,
) -> tuple[Decimal, dict[str, tuple[Decimal, str]]]:
    """Solve the SAFE conversion. Returns the exact pool top-up X and, per instrument, its
    conversion price and the term that set it."""
    best = best_terms(convs)
    # Guard: post-money caps that own the whole company can never convert. An MFN instrument is
    # counted at the tightest post-money cap it could adopt.
    k = ZERO
    for c in convs:
        post_cap = best[c.key].post_cap
        if post_cap:
            k += amounts[c.key] / d(post_cap)
    if k >= 1:
        raise EngineError(
            "SAFE_CAPS_EXCEED",
            "The post-money SAFE caps add up to 100% or more of the company.",
        )

    def terms(sigma: Decimal) -> tuple[Decimal, Decimal, dict[str, tuple[Decimal, str]]]:
        # pool top-up X and pre-money capitalisation A for this guess of the SAFE shares
        x = max(ZERO, (m * (s0d + sigma) - d(u0)) / (1 - m))
        a = s0d + sigma + x
        price = v / a
        per: dict[str, tuple[Decimal, str]] = {}
        for c in convs:
            options: list[tuple[Decimal, str]] = [(price, "round")]
            t = best[c.key]
            # post-money: capitalisation = company + all converting shares, but not the new pool.
            # pre-money: the company + the pool created in this financing, not the converting
            # shares.
            if t.post_cap:
                options.append((d(t.post_cap) / (s0d + sigma), "cap"))
            if t.pre_cap:
                options.append((d(t.pre_cap) / (s0d + x), "cap"))
            if t.discount_bps:
                options.append((price * (BPS - d(t.discount_bps)) / BPS, "discount"))
            per[c.key] = min(options, key=lambda o: o[0])
        return x, price, per

    def shares_for(sigma: Decimal) -> Decimal:
        _, _, per = terms(sigma)
        return sum((amounts[c.key] / per[c.key][0] for c in convs), ZERO)

    # sigma (all converting shares) solves sigma = f(sigma). f is increasing, convex and piecewise
    # linear (each term is the best of several linear options), so Newton's method from 0 climbs
    # monotonically to the smallest root and ends in a few steps. If the slope of f reaches 1
    # there is no root: the converting instruments would own the whole company.
    sigma = ZERO
    for _ in range(MAX_ITERATIONS):
        f0 = shares_for(sigma)
        gap = f0 - sigma
        if abs(gap) <= CONVERGED * max(Decimal(1), f0):
            break
        h = max(Decimal(1), sigma) * Decimal("1e-9")
        slope = (shares_for(sigma + h) - f0) / h
        if slope >= 1 - Decimal("1e-12"):
            raise EngineError(
                "CONVERSION_DID_NOT_SETTLE",
                "At these terms the SAFEs and notes would own all of the company, so they cannot "
                "convert. Check the caps, discounts and the pre-money valuation.",
            )
        sigma = sigma + gap / (1 - slope)
    else:
        raise EngineError(
            "CONVERSION_DID_NOT_SETTLE", "The SAFE and note conversion did not settle."
        )

    x_exact, _, per = terms(sigma)
    return x_exact, per


def _apply_round(
    positions: list[Position],
    convertibles: list[Convertible],
    round_: RoundInput,
    as_of: date,
    index: int,
) -> tuple[list[Position], StepResult]:
    _check(round_)
    s0 = sum(p.shares for p in positions)
    if s0 <= 0:
        raise EngineError("EMPTY_CAP_TABLE", "Add your founders' shares before modelling a round.")
    u0 = sum(p.shares for p in positions if p.kind == "pool")
    v, n = d(round_.pre_money_minor), d(round_.new_money_minor)
    t = d(round_.pool_target_bps) / BPS
    m = t * (v + n) / v
    if m >= 1:
        raise EngineError(
            "POOL_TARGET_TOO_HIGH",
            "That option pool is bigger than the existing holders' share of the post-money.",
        )
    convs = list(convertibles)
    amounts = {c.key: d(converting_amount(c, as_of)) for c in convs}
    x_exact, per = _solve(convs, amounts, d(s0), u0, v, m)
    conversions = []
    new_positions = list(positions)
    safe_total = 0
    for c in convs:
        price, term = per[c.key]
        shares = floor_int(amounts[c.key] / price)
        safe_total += shares
        conversions.append(
            {
                "ref": c.key,
                "name": c.name,
                "security": c.security,
                "amount_minor": int(amounts[c.key]),
                "term": term,
                "conversion_price": money_str(price),
                "shares": shares,
            }
        )
        # Converted shares belong to the HOLDER (their row, their kind); the entry id stays in
        # `ref` above, so one person with stock and a SAFE is one line in the table.
        new_positions.append(
            Position(c.holder_key or c.key, c.name, c.holder_kind, "preferred", shares)
        )
    top_up = ceil_int(x_exact) if x_exact > 0 else 0
    if top_up:
        pool_key = next((p.key for p in positions if p.kind == "pool"), "pool")
        new_positions.append(Position(pool_key, "Option pool", "pool", "option", top_up))
    pre_shares = s0 + safe_total + top_up
    price = v / d(pre_shares)
    investors = floor_int(n / price)
    new_positions.append(
        Position(f"round-{index}", f"{round_.name} investors", "investor", "preferred", investors)
    )
    before = _founder_bps(positions)
    after = _founder_bps(new_positions)
    step = StepResult(
        name=round_.name,
        pre_money_minor=round_.pre_money_minor,
        new_money_minor=round_.new_money_minor,
        post_money_minor=round_.pre_money_minor + round_.new_money_minor,
        price_per_share=money_str(price),
        pre_round_shares=s0,
        pool_top_up_shares=top_up,
        new_investor_shares=investors,
        post_round_shares=pre_shares + investors,
        conversions=conversions,
        founders_before_bps=before,
        founders_after_bps=after,
    )
    return new_positions, step


def _founder_bps(positions: list[Position]) -> int:
    holders = holder_shares(positions, fully_diluted=True)
    bps = largest_remainder_bps(holders)
    founders = {p.key for p in positions if p.kind == "founder"}
    return sum(v for k, v in bps.items() if k in founders)


def model_rounds(
    positions: list[Position],
    convertibles: list[Convertible],
    rounds: list[RoundInput],
    *,
    as_of: date,
) -> tuple[list[Position], list[StepResult]]:
    """Apply rounds one after another. Convertibles convert in the first round only; later rounds
    dilute everyone, including the earlier investors."""
    if not 1 <= len(rounds) <= MAX_ROUNDS:
        raise EngineError("INVALID_ROUND", f"Model between 1 and {MAX_ROUNDS} rounds.")
    steps = []
    current, pending = positions, convertibles
    for i, r in enumerate(rounds, start=1):
        current, step = apply_round(current, pending, r, as_of=as_of, index=i)
        steps.append(step)
        pending = []
    return current, steps


def ownership_table(positions: list[Position]) -> list[dict[str, Any]]:
    """Fully-diluted ownership per holder, shares and basis points summing to 10 000."""
    shares = holder_shares(positions, fully_diluted=True)
    bps = largest_remainder_bps(shares)
    names: dict[str, tuple[str, str]] = {}
    for p in positions:
        names.setdefault(p.key, (p.name, p.kind))
    rows = [
        {"ref": k, "name": names[k][0], "kind": names[k][1], "shares": shares[k], "bps": bps[k]}
        for k in shares
    ]
    return sorted(rows, key=lambda r: (-r["shares"], r["name"], r["ref"]))
