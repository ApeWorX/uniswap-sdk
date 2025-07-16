from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from ape.types import AddressType
from ape_tokens import TokenInstance
from eth_utils import to_int
from eth_utils.address import is_checksum_address

from .types import BasePair, ConvertsToToken, Fee, Route

if TYPE_CHECKING:
    pass


def get_token_address(token: ConvertsToToken) -> AddressType:
    if is_checksum_address(token):
        return token

    from ape import convert

    return convert(token, AddressType)


def sort_tokens(
    tokens: tuple[ConvertsToToken, ConvertsToToken],
) -> tuple[ConvertsToToken, ConvertsToToken]:
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


def get_total_fee(route: Route) -> Decimal:
    """Compute the cumulative fee of the route"""

    ratio = Decimal(1)  # start w/ 1 = No loss

    for pair in route:
        # NOTE: Every hop in the route accrues the pair's fee
        ratio *= 1 - pair.fee / Decimal(Fee.MAXIMUM)

    # NOTE: Doing it as a ratio as it's cleaner to understand
    return 1 - ratio  # "fee" is the delta between the loss ratio and 1


def convert_flows_to_routes(
    flows: dict[AddressType, dict[AddressType, dict[Any, int]]],
    start: AddressType,
    end: AddressType,
    get_pair: Callable[[AddressType, AddressType, Any], "BasePair"],
    execute_in_reverse: bool = False,
) -> Iterator[tuple[Route, int]]:
    # Convert NetworkX "flowDict" to `Solution`
    # `Flow` solution layout is `{Token => {Token => {Key => Int}}}`
    # `Solution` layout needs to be `{(Pair, ...): Amount}`
    # NOTE: Flow can contain `Amount = 0` or can be an empty mapping, so filter that out
    for token, key_amount in flows[start].items():
        for key, amount in key_amount.items():
            if amount == 0:
                continue

            pair = get_pair(start, token, key)

            if token == end:
                yield (pair,), amount
                continue  # NOTE: No need to recurse further

            for inner_flow, inner_amount in convert_flows_to_routes(
                flows, token, end, get_pair, execute_in_reverse=execute_in_reverse
            ):
                if execute_in_reverse:
                    yield (*inner_flow, pair), min(amount, inner_amount)
                else:
                    yield (pair, *inner_flow), min(amount, inner_amount)
