from decimal import Decimal

from ape.types import AddressType
from ape_tokens import TokenInstance
from eth_utils import to_int

from .types import Route


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


def get_liquidity(token: TokenInstance, route) -> Decimal:
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
