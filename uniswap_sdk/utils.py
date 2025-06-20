from decimal import Decimal
from typing import TYPE_CHECKING

from ape.types import AddressType
from ape_tokens import TokenInstance
from eth_utils import to_int

from .types import Route, Solution

if TYPE_CHECKING:
    from .universal_router import Plan


def get_token_address(token):
    from ape import convert

    return convert(token, AddressType)


def sort_tokens(tokens):
    a, b = tokens
    addr_a_int = to_int(hexstr=get_token_address(a))
    addr_b_int = to_int(hexstr=get_token_address(b))
    return (a, b) if (addr_a_int < addr_b_int) else (b, a)


def price_to_tick(price: Decimal) -> int:
    # NOTE: `log_b(a)` can be written as `ln(b) / ln(a)`
    return int(price.ln() / Decimal("1.0001").ln())


def tick_to_price(tick: int) -> Decimal:
    return Decimal("1.0001") ** tick


def get_price(token: TokenInstance, route: Route) -> Decimal:
    price = Decimal(1)

    for pair in route:
        price *= pair.price(token)
        token = pair.other(token)

    return price


def get_liquidity(token: TokenInstance, route: Route) -> Decimal:
    price = Decimal(1)
    liquidity = Decimal("inf")

    for pair in route:
        liquidity = min(liquidity, pair.liquidity[token] / price)
        try:
            price *= pair.price(token)
        except ValueError:  # Uninitialized Pool or Zero Liquidity
            return Decimal(0)

        token = pair.other(token)

    assert liquidity != Decimal("inf")
    return liquidity


def convert_solution_to_plan(
    have: TokenInstance,
    want: TokenInstance,
    solution: Solution,
    total_amount_in: Decimal,
    total_amount_out: Decimal,
) -> "Plan":
    from . import universal_router as ur
    from . import v2, v3

    plan = ur.Plan()

    for route, amount_in_route in solution.items():
        if all(isinstance(p, v3.Pool) for p in route):
            plan = plan.v3_swap_exact_in(
                ur.Constants.MSG_SENDER,
                int(amount_in_route * 10 ** have.decimals()),
                # NOTE: Percentage of `total_amount_out` that should come from swap
                int((amount_in_route / total_amount_in) * total_amount_out) * 10 ** want.decimals(),
                v3.Factory.encode_route(have, *route),
                False,  # PayerIsUser
            )

        elif all(isinstance(p, v2.Pair) for p in route):
            plan = plan.v2_swap_exact_in(
                ur.Constants.MSG_SENDER,
                int(amount_in_route * 10 ** have.decimals()),
                # NOTE: Percentage of `total_amount_out` that should come from swap
                int((amount_in_route / total_amount_in) * total_amount_out) * 10 ** want.decimals(),
                v2.Factory.encode_route(have, *route),
                False,  # PayerIsUser
            )

        else:
            # NOTE: Should never happen
            raise ValueError(f"Invalid route: {route}")

    return plan
