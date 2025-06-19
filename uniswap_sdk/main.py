from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable

from ape.logging import logger
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from ape_tokens import Token, TokenInstance

from . import universal_router as ur
from . import v2, v3
from .solver import solve as default_solver
from .types import BaseIndex, BasePair, Route, Solution, Solver
from .utils import convert_solution_to_plan, get_liquidity, get_price

if TYPE_CHECKING:
    from ape.api import BaseAddress, ReceiptAPI
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
        use_v3: bool = True,
        use_v4: bool = False,
        use_solver: Solver | None = None,
    ):
        self.router = ur.UniversalRouter()
        self.solver = use_solver or default_solver

        self.indexers: list[BaseIndex] = []

        if use_v1:
            raise ValueError("Uniswap v1 not supported yet.")

        if use_v2:
            self.indexers.append(v2.Factory())

        if use_v3:
            self.indexers.append(v3.Factory())

        if use_v4:
            raise ValueError("Uniswap v4 not supported yet.")

        if not self.indexers:
            raise ValueError("Must enable at least one version of the protocol to use this class")

    def index(
        self,
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator[BasePair]:
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
            yield from indexer.index(tokens=tokens, min_liquidity=min_liquidity)

    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ):

        for indexer in self.indexers:
            indexer.install(bot, tokens=tokens, min_liquidity=min_liquidity)

    # cachetools.cached w/ ttl set to block-time?
    def price(
        self,
        base: TokenInstance | AddressType,
        quote: TokenInstance | AddressType,
        min_liquidity: Decimal = Decimal(1),  # 1 token
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
                if (liquidity := get_liquidity(base, route)) < min_liquidity:
                    continue  # Skip this route (NOTE: `get_price` will raise)
                price_quotient += get_price(base, route) * liquidity
                total_liquidity += liquidity

        if total_liquidity == Decimal(0):
            raise RuntimeError("Could not solve, not enough liquidity")

        return price_quotient / total_liquidity

    def process_args(
        self,
        have: TokenInstance | AddressType,
        want: TokenInstance | AddressType,
        amount_in: Decimal | str | int | None = None,
        amount_out: Decimal | str | int | None = None,
        max_amount_in: Decimal | str | int | None = None,
        min_amount_out: Decimal | str | int | None = None,
        slippage: Decimal = Decimal(0.005),
    ) -> tuple[TokenInstance, TokenInstance, Decimal, Decimal]:
        if not isinstance(have, TokenInstance):
            have = Token.at(have)

        if not isinstance(want, TokenInstance):
            want = Token.at(want)

        if not isinstance(amount_in, Decimal):
            amount_in = Decimal(self.conversion_manager.convert(amount_in or 0, int)) / Decimal(
                10 ** have.decimals()
            )

        if not isinstance(amount_out, Decimal):
            amount_out = Decimal(self.conversion_manager.convert(amount_out or 0, int)) / Decimal(
                10 ** want.decimals()
            )

        if not isinstance(max_amount_in, Decimal):
            max_amount_in = Decimal(
                self.conversion_manager.convert(max_amount_in or 0, int)
            ) / Decimal(10 ** have.decimals())

        if not isinstance(min_amount_out, Decimal):
            min_amount_out = Decimal(
                self.conversion_manager.convert(min_amount_out or 0, int)
                / Decimal(10 ** want.decimals())
            )

        # NOTE: All the values are now `Decimal`
        if not (amount_in or amount_out) or (amount_in and amount_out):
            raise ValueError("Must specify exactly one of `amount_in` or `amount_out`")

        elif max_amount_in and min_amount_out:
            raise ValueError("Must specify exactly one of `max_amount_in` or `min_amount_out`")

        elif amount_in and max_amount_in:
            raise ValueError("Cannot specify both `amount_in` and `max_amount_in`")

        elif amount_out and min_amount_out:
            raise ValueError("Cannot specify both `amount_out` and `min_amount_out`")

        # Compute worst-case price (lowest) to pay for `want` in terms of `have`
        if max_amount_in:
            min_price = amount_out / max_amount_in

        elif min_amount_out:
            min_price = min_amount_out / amount_in

        elif not (0 < slippage < 1):
            raise RuntimeError(f"Invalid slippage: {slippage}")

        else:  # Don't pay more for `want` than current price - (fee + slippage)
            min_price = self.price(have, want) * (1 - slippage)

        # NOTE: warn user about common usage errors (but don't raise, let transaction raise)
        if min_price > (market_price := self.price(have, want)):
            logger.warning(
                "Swap might fail: "
                f"Min price '{min_price:0.6f}' higher than market + fee '{market_price:0.6f}'"
            )

        return have, want, amount_in or max_amount_in, amount_out or min_amount_out

    def find_routes(
        self,
        have: TokenInstance | AddressType,
        want: TokenInstance | AddressType,
    ) -> Iterator[Route]:
        for indexer in self.indexers:
            for route in indexer.find_routes(have, want):
                yield route

    def solve(
        self,
        have: TokenInstance | AddressType,
        want: TokenInstance | AddressType,
        amount_in: Decimal | str | int | None = None,
        amount_out: Decimal | str | int | None = None,
        max_amount_in: Decimal | str | int | None = None,
        min_amount_out: Decimal | str | int | None = None,
        slippage: Decimal = Decimal(0.005),
    ) -> Solution:
        have, want, amount_in, amount_out = self.process_args(
            have, want, amount_in, amount_out, max_amount_in, min_amount_out
        )
        routes = self.find_routes(have=have, want=want)
        return self.solver(have, amount_in, *routes)

    def create_plan(
        self,
        have: TokenInstance | AddressType,
        want: TokenInstance | AddressType,
        amount_in: Decimal | str | int | None = None,
        amount_out: Decimal | str | int | None = None,
        max_amount_in: Decimal | str | int | None = None,
        min_amount_out: Decimal | str | int | None = None,
        slippage: Decimal = Decimal(0.005),
        receiver: "str | BaseAddress | AddressType | None" = None,
    ) -> ur.Plan:
        have, want, amount_in, amount_out = self.process_args(
            have, want, amount_in, amount_out, max_amount_in, min_amount_out, slippage
        )

        solution = self.solve(
            have=have,
            want=want,
            amount_in=amount_in,
            amount_out=amount_out,
            max_amount_in=max_amount_in,
            min_amount_out=min_amount_out,
            slippage=slippage,
        )

        return convert_solution_to_plan(have, want, solution, amount_in, amount_out)

    def swap(
        self,
        have: TokenInstance | AddressType,
        want: TokenInstance | AddressType,
        amount_in: Decimal | str | int | None = None,
        amount_out: Decimal | str | int | None = None,
        max_amount_in: Decimal | str | int | None = None,
        min_amount_out: Decimal | str | int | None = None,
        receiver: "str | BaseAddress | AddressType | None" = None,
        slippage: Decimal = Decimal(0.005),
        as_transaction: bool = False,
        deadline: timedelta | None = None,
        **txn_kwargs,
    ) -> "ReceiptAPI":
        plan = self.create_plan(
            have=have,
            want=want,
            amount_in=amount_in,
            amount_out=amount_out,
            max_amount_in=max_amount_in,
            min_amount_out=min_amount_out,
            slippage=slippage,
        )

        return self.router.execute(plan, deadline=deadline, **txn_kwargs)
