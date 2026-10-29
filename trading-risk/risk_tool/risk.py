"""Pure cash-risk rules for the intraday and equal-share swing allocations."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import (
    Context, Decimal, DivisionByZero, InvalidOperation, MAX_EMAX, MIN_EMIN,
    Overflow, ROUND_HALF_EVEN, ROUND_HALF_UP, Underflow, localcontext,
)
from re import fullmatch


_OPENING_FRACTION = Decimal("0.06")
_INTRADAY_FRACTION = Decimal("0.02")


@contextmanager
def _arithmetic_context() -> Iterator[None]:
    context = Context(
        prec=28, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX,
        traps=[DivisionByZero, InvalidOperation, Overflow, Underflow],
    )
    try:
        with localcontext(context):
            yield
    except (Overflow, Underflow) as error:
        raise ValueError("calculation inputs exceed supported Decimal arithmetic range") from error


def _validate_decimal(value: Decimal, field: str, *, nonnegative: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be nonnegative")


@dataclass(frozen=True)
class MonthlyRiskSettings:
    """Monthly INR allocations; the swing pool is divided by swing_slots."""

    month: str
    intraday_allocation: Decimal
    swing_allocation: Decimal
    swing_slots: int

    def __post_init__(self) -> None:
        if (not isinstance(self.month, str)
                or fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", self.month) is None
                or self.month.startswith("0000")):
            raise ValueError("month must be YYYY-MM with a valid year and month")
        _validate_decimal(self.intraday_allocation, "intraday_allocation", nonnegative=True)
        _validate_decimal(self.swing_allocation, "swing_allocation", nonnegative=True)
        if (not isinstance(self.swing_slots, int)
                or isinstance(self.swing_slots, bool) or self.swing_slots <= 0):
            raise ValueError("swing_slots must be a positive integer")


@dataclass(frozen=True)
class RiskQuote:
    """Source settings and raw amounts, without presentation rounding."""

    settings: MonthlyRiskSettings
    current_accum: Decimal
    intraday_r_multiplier: Decimal
    combined_monthly_allocation: Decimal
    intraday_base_risk: Decimal
    intraday_risk: Decimal
    swing_risk_per_position: Decimal


def estimate_opening_accum(intraday_allocation: Decimal) -> Decimal:
    """Estimate starting funds as the monthly intraday allocation divided by 6%."""
    _validate_decimal(intraday_allocation, "intraday_allocation", nonnegative=True)
    with _arithmetic_context():
        return intraday_allocation / _OPENING_FRACTION


def accum_from_outcomes(
    opening_accum: Decimal, intraday_net_outcomes: Iterable[Decimal]
) -> Decimal:
    """Add the supplied latest preceding intraday outcomes once, with their signs.

    The caller selects/updates trade outcomes and excludes swing results. This
    provider does not keep a ledger or decide whether an outcome is realized.
    """
    _validate_decimal(opening_accum, "opening_accum")
    try:
        outcomes = iter(intraday_net_outcomes)
    except TypeError as error:
        raise ValueError("intraday_net_outcomes must be an iterable of Decimals") from error
    with _arithmetic_context():
        accum = opening_accum
        for index, outcome in enumerate(outcomes):
            _validate_decimal(outcome, f"intraday_net_outcomes[{index}]")
            accum += outcome
        return accum


def calculate_risk(
    settings: MonthlyRiskSettings,
    *,
    current_accum: Decimal,
    intraday_r_multiplier: Decimal,
) -> RiskQuote:
    """Quote 2% of signed Accum times manual R, plus an independent swing share."""
    if not isinstance(settings, MonthlyRiskSettings):
        raise ValueError("settings must be MonthlyRiskSettings")
    _validate_decimal(current_accum, "current_accum")
    _validate_decimal(intraday_r_multiplier, "intraday_r_multiplier", nonnegative=True)
    with _arithmetic_context():
        base_risk = current_accum * _INTRADAY_FRACTION
        return RiskQuote(
            settings=settings,
            current_accum=current_accum,
            intraday_r_multiplier=intraday_r_multiplier,
            combined_monthly_allocation=settings.intraday_allocation + settings.swing_allocation,
            intraday_base_risk=base_risk,
            intraday_risk=base_risk * intraday_r_multiplier,
            swing_risk_per_position=settings.swing_allocation / settings.swing_slots,
        )


def format_money(amount: Decimal) -> str:
    """Format a signed INR amount to paise, without a currency symbol or grouping.

    Presentation rounds half up; callers keep using the original raw amount
    for subsequent calculations.
    """
    _validate_decimal(amount, "amount")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_UP)):
        return format(amount, ".2f")
