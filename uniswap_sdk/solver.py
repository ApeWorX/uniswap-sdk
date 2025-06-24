from collections.abc import Iterator
from decimal import Decimal
from typing import Callable, Iterable

import networkx as nx  # type: ignore[import-untyped]
from ape.types import AddressType
from ape_tokens import TokenInstance

from . import universal_router as ur
from . import v2, v3
from .types import ExactInOrder, Order, Route
from .utils import get_total_fee

Solution = dict[Route, Decimal]
SolverType = Callable[[Order, Iterable[Route]], Solution]


# NOTE: Should match `SolverType` above
def solve(order: Order, routes: Iterable[Route]) -> Solution:
    """Default solver algorithm: Return a Solution that maximizes `order`'s sale price."""

    ONE_HAVE_TOKEN = Decimal(10 ** order.have_token.decimals())
    G = nx.MultiDiGraph()
    # NOTE: Normalize to units of `have`
    demand = int(order.amount_in if isinstance(order, ExactInOrder) else order.max_amount_in)
    G.add_node(order.have, demand=-demand * ONE_HAVE_TOKEN)
    G.add_node(order.want, demand=demand * ONE_HAVE_TOKEN)

    for route in routes:
        token = order.have
        price = Decimal(1)
        liquidity = Decimal("inf")

        for pair in route:
            liquidity = min(liquidity, pair.liquidity[token] / price)
            try:
                price *= pair.price(token)
            except ValueError:  # Uninitialized Pool or Zero Liquidity
                break  # Skip to next route

            # NOTE: `NetworkX` algos do not work w/ Decimals, only integers
            G.add_edge(
                token,
                # NOTE: Directed graph is tokenA -> tokenB (set here via :=)
                (token := pair.other(token)).address,
                # NOTE: Edge key must be globally-unique, or will be overwritten
                key=pair.key,
                # `capacity` represents the "max demand" that can flow through edge (must be int)
                capacity=int((depth := pair.depth(token, order.slippage)) / price * ONE_HAVE_TOKEN),
                # `weight` represents the "cost" of 1 unit of flow (must be int)
                # TODO: Account for gas costs too
                weight=int(
                    # NOTE: Convert `reflexivity` % to bips (must be integer and match fee)
                    10_000
                    * (
                        order.slippage  # NOTE: We already used this to calculate `depth`
                        if depth < demand * price
                        # NOTE: Calculate reflexivity "cost" only if pair has enough depth
                        else pair.reflexivity(token, demand * price)
                    )
                ),
                pair=pair,  # NOTE: Keep this around for converting solution back to routes
            )

    try:
        flows = nx.min_cost_flow(G)
    except nx.NetworkXUnfeasible:
        raise RuntimeError("Solver failure")

    # Convert NetworkX "flowDict" to `Solution`
    # `Flow` solution layout is `{Token => {Token => {Key => Int}}}`
    # `Solution` layout needs to be `{(Pair, ...): Amount}`
    # NOTE: Flow can contain `Amount = 0` or can be an empty mapping, so filter that out
    def convert_to_routes(start: AddressType, end: AddressType) -> Iterator[tuple[Route, Decimal]]:
        for token, key_amount in flows[start].items():
            for key, amount in key_amount.items():
                if amount == 0:
                    continue

                pair = G[start][token][key].get("pair")
                # NOTE: Adjust integer result from `flow` back to decimals
                amount /= ONE_HAVE_TOKEN

                if token == end:
                    yield (pair,), amount
                    continue  # NOTE: No need to recurse further

                for inner_flow, inner_amount in convert_to_routes(token, end):
                    yield (pair, *inner_flow), min(amount, inner_amount)

    if (
        sum((solution := dict(convert_to_routes(order.have, order.want))).values())
        != order.amount_in
    ):
        # NOTE: Shouldn't happen if algo is correct
        raise RuntimeError("Solver failure")

    return solution


def convert_solution_to_plan(
    solution: Solution,
    have: TokenInstance,
    want: TokenInstance,
    total_amount_out: Decimal = Decimal(0),
    use_exact_in: bool = True,
    receiver: AddressType | None = None,
) -> ur.Plan:
    ONE_HAVE_TOKEN = 10 ** have.decimals()
    ONE_WANT_TOKEN = 10 ** want.decimals()
    total_amount_in = sum(solution.values())

    plan = ur.Plan()
    for route, amount_in_route in solution.items():
        total_fee = get_total_fee(route)

        # NOTE: Percentage of `total_amount_out` that should come from this swap
        amount_out_route = int(
            total_amount_out
            * (amount_in_route / total_amount_in)
            * (1 - total_fee)
            * ONE_WANT_TOKEN
        )
        amount_in_route = int(amount_in_route * ONE_HAVE_TOKEN)  # type: ignore[assignment]

        if all(isinstance(p, v3.Pool) for p in route):
            plan = (plan.v3_swap_exact_in if use_exact_in else plan.v3_swap_exact_out)(
                receiver or ur.Constants.MSG_SENDER,
                # NOTE: If `exact_in` this gets interpretted as "exact in", else "max in"
                amount_in_route,
                amount_out_route,
                v3.Factory.encode_route(have, *route),
                True,  # PayerIsUser (False = Payer is Router)
            )

        elif all(isinstance(p, v2.Pair) for p in route):
            plan = (plan.v2_swap_exact_in if use_exact_in else plan.v2_swap_exact_out)(
                receiver or ur.Constants.MSG_SENDER,
                # NOTE: If `exact_in` this gets interpretted as "exact in", else "max in"
                amount_in_route,
                amount_out_route,
                v2.Factory.encode_route(have, *route),
                True,  # PayerIsUser (False = Payer is Router)
            )

        else:
            # NOTE: Should never happen
            raise ValueError(f"Invalid route: {route}")

    return plan
