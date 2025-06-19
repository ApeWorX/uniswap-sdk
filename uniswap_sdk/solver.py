from decimal import Decimal
from functools import partial

from ape_tokens import TokenInstance

from .types import Route, Solution
from .utils import get_liquidity, get_price


def solve(token: TokenInstance, needed: Decimal, *routes: Route) -> Solution:
    """Naive solver algorithm"""

    # TODO: Use gradient descent algo
    # TODO: Account for swap fees
    # TODO: Account for gas costs
    # TODO: Account for slippage (1/reflexivity)
    solution: Solution = {}
    for route in sorted(routes, key=partial(get_price, token)):
        amount = get_liquidity(token, route)
        solution[min(amount, needed)] = route
        needed -= amount
        if needed <= Decimal(0):
            break
    else:
        raise RuntimeError("Could not solve")

    return solution
