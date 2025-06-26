from decimal import Decimal
from typing import Callable, Iterable

import networkx as nx  # type: ignore[import-untyped]
from ape.types import AddressType

from . import universal_router as ur
from . import v2, v3
from .types import ExactInOrder, ExactOutOrder, Order, Route
from .utils import convert_flows_to_routes

Solution = dict[Route, Decimal]
"""Mapping of route to ratio of amount to process with that route"""

SolverType = Callable[[Order, Iterable[Route]], Solution]
"""Type of a solver function or method"""


# NOTE: Should match `SolverType` above
def solve(order: Order, routes: Iterable[Route]) -> Solution:
    """Default solver algorithm: Return a Solution that maximizes `order`'s sale price."""

    # NOTE: An ExactOutOrder is executed in reverse, so everything is flipped
    if execute_in_reverse := isinstance(order, ExactOutOrder):
        start_token, end_token = order.want_token, order.have_token
        demand = order.amount_out
        routes_iter = map(reversed, routes)

    else:
        start_token, end_token = order.have_token, order.want_token
        demand = order.amount_in
        routes_iter = iter(routes)  # type: ignore[arg-type]

    ONE_START_TOKEN = 10 ** start_token.decimals()
    G = nx.MultiDiGraph()
    # NOTE: Normalize to units of `start_token`
    G.add_node(start_token.address, demand=-int(demand * ONE_START_TOKEN))
    G.add_node(end_token.address, demand=int(demand * ONE_START_TOKEN))

    for route in routes_iter:
        token = start_token
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
                token.address,
                # NOTE: Directed graph is tokenA -> tokenB (set here via :=)
                (token := pair.other(token)).address,
                # NOTE: Edge key must be globally-unique, or will be overwritten
                key=pair.key,
                # `capacity` represents the "max demand" that can flow through edge (must be int)
                capacity=int(
                    ((depth := pair.depth(token, order.slippage)) / price) * ONE_START_TOKEN
                ),
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
    except nx.NetworkXUnfeasible as err:
        raise RuntimeError(f"Solver failure: {err}")

    # NOTE: Flatten the result from NetworkX into our preferred `Solution` type
    solution = dict(
        convert_flows_to_routes(
            flows,
            start_token.address,
            end_token.address,
            lambda start, token, key: G[start][token][key].get("pair"),
            execute_in_reverse=execute_in_reverse,
        )
    )

    # Convert absolute amounts to percentages by dividing by total `start_token` demand
    # NOTE: Reason we return percentage and not absolute is to avoid the need for fee conversion.
    #       Fees are accounted for in "slippage", guarded by `min_amount_out`/`max_amount_in` when
    #       the order is converted into a Path. The solution provided by NetworkX does not have a
    #       way of adding "loss" into the actual result, although it is considered via the "weight"
    #       parameter (which combines slippage + fee + gas costs), and it is best not to try and
    #       account for it here, as conditions may change slightly between when the solution is
    #       computed and when it actually gets traded.
    return {
        # NOTE: Adjust integer result from `flow` back in terms of `start_token` decimals
        route: Decimal(amount) / Decimal(ONE_START_TOKEN) / demand
        for route, amount in solution.items()
    }


def convert_solution_to_plan(
    order: Order,
    solution: Solution,
    permit_step: ur.Command | None = None,
    receiver: AddressType | None = None,
) -> ur.Plan:
    ONE_HAVE_TOKEN = 10 ** order.have_token.decimals()
    ONE_WANT_TOKEN = 10 ** order.want_token.decimals()

    use_exact_in = isinstance(order, ExactInOrder)
    total_amount_in = order.amount_in if use_exact_in else order.max_amount_in
    total_amount_out = order.min_amount_out if use_exact_in else order.amount_out

    plan = ur.Plan()
    if permit_step:
        plan = plan.add(permit_step)

    # TODO: Add donation support
    if receiver is None:
        receiver = ur.Constants.MSG_SENDER

    payer_is_user = True  # False = Payer is Router

    for route, flow_ratio in solution.items():
        # Percentage of `total_amount_in/_out` that should come from this swap
        amount_in_route = total_amount_in * flow_ratio
        amount_out_route = total_amount_out * flow_ratio

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
