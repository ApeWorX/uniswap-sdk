from decimal import Decimal

from ape_tokens import TokenInstance

from .types import Route


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
