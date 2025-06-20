from collections.abc import Iterator
from decimal import Decimal

import networkx as nx  # type: ignore[import-untyped]
from ape.types import AddressType
from ape_tokens import TokenInstance

from .types import Route, Solution


def solve(have: TokenInstance, want: TokenInstance, amount_in: Decimal, *routes: Route) -> Solution:
    """Default solver algorithm: Return a Solution that maximizes `sum(solution.values())`."""

    ONE_HAVE_TOKEN = Decimal(10 ** have.decimals())
    G = nx.MultiDiGraph()
    # NOTE: Normalize to units of `have`
    G.add_node(have.address, demand=-int(amount_in * ONE_HAVE_TOKEN))
    G.add_node(want.address, demand=int(amount_in * ONE_HAVE_TOKEN))

    for route in routes:
        token = have
        price = Decimal(1)
        liquidity = Decimal("inf")

        for pair in route:
            liquidity = min(liquidity, pair.liquidity[token] / price)
            try:
                price *= pair.price(token)
            except ValueError:  # Uninitialized Pool or Zero Liquidity
                break  # Skip to next route

            G.add_edge(
                token.address,
                # NOTE: Directed graph is tokenA -> tokenB (set here via :=)
                (token := pair.other(token)).address,
                # TODO: Add globally-unique key property (across all 4 versions) to `BasePair`
                key=pair.address,
                # TODO: Correctly determine `weight` from slippage reflexivity + fee
                # TODO: Account for gas costs
                # weight=pair.swap_cost
                weight=int(pair.fee),  # NOTE: `fee` is already "per unit weight"
                # TODO: Correctly determine `capacity` from pair depth (% slippage for size in mbps)
                # capacity=10_000_000 // pair.depth(amount_in / price)
                # NOTE: `NetworkX` algos do not work w/ Decimals, only integers
                capacity=int(liquidity * ONE_HAVE_TOKEN),
                pair=pair,
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

    if sum((solution := dict(convert_to_routes(have.address, want.address))).values()) != amount_in:
        # NOTE: Shouldn't happen if algo is correct
        raise RuntimeError("Solver failure")

    return solution
