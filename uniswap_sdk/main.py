from decimal import Decimal
from typing import TYPE_CHECKING, Iterable

from ape.logging import logger
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from ape_tokens import Token, TokenInstance

from . import universal_router as ur
from . import v2
from .types import BaseIndex
from .utils import get_liquidity, get_price

if TYPE_CHECKING:
    from silverback import SilverbackBot


class Uniswap(ManagerAccessMixin):
    """
    Main class used to work with all Uniswap protocol deployments on current chain for swapping,
    pricing, indexing, solving, and more.

    Example usage::

        >>> from ape_tokens import tokens
        >>> from uniswap_sdk import Uniswap
        >>> uni = Uniswap(use_v3=False)  # Can skip versions and only index certain tokens
        >>> uni.index(tokens=tokens)  # Takes a long time, but makes planning faster
        >>> uni.price("UNI", "USDC")
        Decimal("4.75")
        >>> uni = tokens["UNI"]
        >>> usdc = tokens["USDC"]
        >>> tx = uni.swap(
        ...     uni,
        ...     usdc,
        ...     amount_in="12 UNI",
        ...     slippage=0.3,
        ...     deadline=timedelta(minutes=2),
        ...     sender=trader,
        ... )
    """

    def __init__(
        self,
        use_v1: bool = False,
        use_v2: bool = True,
        use_v3: bool = False,
        use_v4: bool = False,
    ):
        self.router = ur.UniversalRouter()

        self.indexers: list[BaseIndex] = []

        if use_v1:
            raise ValueError("Uniswap v1 not supported yet.")

        if use_v2:
            self.indexers.append(v2.Factory())

        if use_v3:
            raise ValueError("Uniswap v3 not supported yet.")

        if use_v4:
            raise ValueError("Uniswap v4 not supported yet.")

        if not self.indexers:
            raise ValueError("Must enable at least one version of the protocol to use this class")

    def index(
        self,
        tokens: Iterable[TokenInstance | AddressType] | None = None,
    ):
        """
        Index all all factory/singleton deployments for enabled versions of protocol.

        ```{warning}
        This takes significant time, often up to 1 hour.
        ```

        ```{notice}
        It is intended to use either this method or ``.install`` (w/ `Silverback). Never both.
        ```
        """

        for indexer in self.indexers:
            version = indexer.__module__.split(".")[-1]
            logger.info(f"Uniswap {version} - indexing pairs for tokens")
            pairs_indexed = len(list(indexer.index(tokens=tokens)))
            logger.success(f"Uniswap {version} - indexed {pairs_indexed} pairs")

    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[TokenInstance | AddressType] | None = None,
    ):

        for indexer in self.indexers:
            indexer.install(bot, tokens=tokens)

    # cachetools.cached w/ ttl set to block-time?
    def price(
        self,
        base: TokenInstance | AddressType,
        quote: TokenInstance | AddressType,
    ) -> Decimal:
        """
        Get price of ``base`` in terms of ``quote``. For example, ETH/USDC is the price of ETH
        (the base currency) in terms of USDC (the quote currency). Searches across all enabled
        versions of the protocol, using the token pairs that have already indexed by each version.

        ```{notice}
        The price returned by this method is "liquidity-weighted", meaning that routes with higher
        liquidity factor more heavily into the result.
        ```

        Args:
            base (:class:~ape_tokens.TokenInstance | :class:~ape.types.AddressType):
                The currency you want the price of, e.g. the 1st currency in a pair.
            quote (:class:~ape_tokens.TokenInstance | :class:~ape.types.AddressType)
                The currency you want the price in terms of, e.g. the 2nd currency in a pair.

        Returns:
            (Decimal): The requested price
        """
        if not isinstance(quote, TokenInstance):
            quote = Token.at(quote)

        if not isinstance(base, TokenInstance):
            base = Token.at(base)

        price_quotient = Decimal(0)
        total_liquidity = Decimal(0)
        for indexer in self.indexers:
            for route in indexer.find_routes(base, quote):
                if (liquidity := get_liquidity(base, route)).is_zero():
                    continue  # Skip this route (NOTE: `get_price` will raise)
                price_quotient += get_price(base, route) * liquidity
                total_liquidity += liquidity

        return price_quotient / total_liquidity
