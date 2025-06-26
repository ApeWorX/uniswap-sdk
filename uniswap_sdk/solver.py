from collections.abc import Iterator
from decimal import Decimal
from typing import Callable, Iterable

import networkx as nx  # type: ignore[import-untyped]
from ape.types import AddressType

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
    order: Order,
    solution: Solution,
    permit_step: ur.Command | None = None,
    receiver: AddressType | None = None,
) -> ur.Plan:
    ONE_HAVE_TOKEN = 10 ** order.have_token.decimals()
    ONE_WANT_TOKEN = 10 ** order.want_token.decimals()

    use_exact_in = isinstance(order, ExactInOrder)
    total_amount_in = sum(solution.values())
    total_amount_out = order.min_amount_out if use_exact_in else order.amount_out

    plan = ur.Plan()
    if permit_step:
        plan = plan.add(permit_step)

    # TODO: Add donation support
    if receiver is None:
        receiver = ur.Constants.MSG_SENDER

    payer_is_user = True  # False = Payer is Router

    for route, amount_in_route in solution.items():
        total_fee = get_total_fee(route)

        # NOTE: Percentage of `total_amount_out` that should come from this swap
        amount_out_route = int(
            total_amount_out
            * (amount_in_route / total_amount_in)
            * (1 - total_fee)
        )

        if all(isinstance(p, v3.Pool) for p in route):
            if use_exact_in:
                plan = plan.v3_swap_exact_in(
                    receiver,
                    int(amount_in_route * ONE_HAVE_TOKEN),  # amountIn
                    int(amount_out_route * ONE_WANT_TOKEN),  # amountOutMin
                    v3.Factory.encode_route(order.have_token, *route),
                    payer_is_user,
                )

            else:
                plan = plan.v3_swap_exact_out(
                    receiver,
                    int(amount_out_route * ONE_WANT_TOKEN),  # amountOut
                    int(amount_in_route * ONE_HAVE_TOKEN),  # amountInMax
                    v3.Factory.encode_route(order.have_token, *route),
                    payer_is_user,
                )

        elif all(isinstance(p, v2.Pair) for p in route):
            if use_exact_in:
                plan = plan.v2_swap_exact_in(
                    receiver,
                    int(amount_in_route * ONE_HAVE_TOKEN),  # amountIn
                    int(amount_out_route * ONE_WANT_TOKEN),  # amountOutMin
                    v2.Factory.encode_route(order.have_token, *route),
                    payer_is_user,
                )

            else:
                plan = plan.v2_swap_exact_out(
                    receiver,
                    int(amount_out_route * ONE_WANT_TOKEN),  # amountOut
                    int(amount_in_route * ONE_HAVE_TOKEN),  # amountInMax
                    v2.Factory.encode_route(order.have_token, *route),
                    payer_is_user,
                )

        else:
            # NOTE: Should never happen
            raise ValueError(f"Invalid route: {route}")

    return plan
