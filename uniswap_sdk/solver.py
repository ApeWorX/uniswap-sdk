from decimal import Decimal
from functools import partial

from ape_tokens import TokenInstance

from .types import Route, Solution


def solve(token: TokenInstance, needed: Decimal, *routes: Route) -> Solution:
    # TODO: Use gradient descent algo
    solution: Solution = {}
    for route in sorted(routes, key=partial(get_price, token)):
        solution[min(amount, needed)] = route
        needed -= amount
        if needed <= Decimal(0):
            break
    else:
        raise RuntimeError("Could not solve")

    return solution
