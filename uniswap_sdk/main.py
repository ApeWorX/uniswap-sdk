from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable

from ape.logging import logger
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from ape_tokens import Token, TokenInstance

from . import universal_router as ur
from . import v2, v3
from .permit2 import Permit2, PermitDetails
from .solver import Solution, SolverType, convert_solution_to_plan
from .solver import solve as default_solver
from .types import BaseIndex, BasePair, ConvertsToToken, ExactInOrder, ExactOutOrder, Order, Route
from .utils import get_liquidity, get_price

if TYPE_CHECKING:
    from ape.api import BaseAddress, ReceiptAPI, TransactionAPI
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

    DEFAULT_SLIPPAGE: Decimal = Decimal(0.005)  # 0.5%

    def __init__(
        self,
        default_slippage: Decimal | float | None = None,
        use_v1: bool = False,
        use_v2: bool = True,
        use_v3: bool = True,
        use_v4: bool = False,
        use_solver: SolverType | None = None,
    ):
        if default_slippage is not None:
            self.DEFAULT_SLIPPAGE = Decimal(default_slippage).quantize(Decimal("1e-5"))

        self.permit2 = Permit2()
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
        tokens: Iterable[ConvertsToToken] | None = None,
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
            yield from indexer.index(tokens=tokens)

    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[ConvertsToToken] | None = None,
    ):

        for indexer in self.indexers:
            indexer.install(bot, tokens=tokens)

    # cachetools.cached w/ ttl set to block-time?
    def price(
        self,
        base: ConvertsToToken,
        quote: ConvertsToToken,
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
            quote = Token.at(self.conversion_manager.convert(quote, AddressType))

        if not isinstance(base, TokenInstance):
            base = Token.at(self.conversion_manager.convert(base, AddressType))

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

    def find_routes(
        self,
        have: ConvertsToToken,
        want: ConvertsToToken,
    ) -> Iterator[Route]:
        for indexer in self.indexers:
            for route in indexer.find_routes(have, want):
                yield route

    def create_order(
        self,
        have: ConvertsToToken,
        want: ConvertsToToken,
        amount_in: Decimal | str | int | None = None,
        amount_out: Decimal | str | int | None = None,
        max_amount_in: Decimal | str | int | None = None,
        min_amount_out: Decimal | str | int | None = None,
        slippage: Decimal | float | None = None,
    ) -> Order:
        if amount_in and amount_out:
            raise ValueError("Cannot supply both `amount_in=` and `amount_out=`")

        elif amount_in and max_amount_in:
            raise ValueError("Cannot supply both `amount_in=` and `max_amount_in=`")

        elif amount_out and min_amount_out:
            raise ValueError("Cannot supply both `amount_out=` and `min_amount_out=`")

        if not isinstance(have, TokenInstance):
            have = Token.at(self.conversion_manager.convert(have, AddressType))
        assert isinstance(have, TokenInstance)  # mypy happy

        if not isinstance(want, TokenInstance):
            want = Token.at(self.conversion_manager.convert(want, AddressType))
        assert isinstance(want, TokenInstance)  # mypy happy

        if amount_in and not isinstance(amount_in, Decimal):
            amount_in = Decimal(self.conversion_manager.convert(amount_in, int)) / Decimal(
                10 ** have.decimals()
            )

        if max_amount_in and not isinstance(max_amount_in, Decimal):
            max_amount_in = Decimal(self.conversion_manager.convert(max_amount_in, int)) / Decimal(
                10 ** have.decimals()
            )

        if amount_out and not isinstance(amount_out, Decimal):
            amount_out = Decimal(self.conversion_manager.convert(amount_out, int)) / Decimal(
                10 ** want.decimals()
            )

        if min_amount_out and not isinstance(min_amount_out, Decimal):
            min_amount_out = Decimal(
                self.conversion_manager.convert(min_amount_out, int)
            ) / Decimal(10 ** want.decimals())

        if slippage is None:
            slippage = self.DEFAULT_SLIPPAGE

        elif not isinstance(slippage, Decimal):
            slippage = Decimal(slippage)

        if amount_out:
            if not max_amount_in:
                min_price = self.price(have, want) * (1 - slippage)
                max_amount_in = amount_out / min_price

            else:  # NOTE: Compute slippage (for solver) based on provided inputs
                slippage = (
                    ((price := self.price(have, want)) - amount_out / max_amount_in) / price
                ).quantize(Decimal("1e-5"))

            return ExactOutOrder(
                have=have,
                want=want,
                amount_out=amount_out,
                max_amount_in=max_amount_in,
                slippage=slippage,
            )

        elif amount_in:
            if not min_amount_out:
                min_price = self.price(have, want) * (1 - slippage)
                min_amount_out = amount_in * min_price

            else:  # NOTE: Compute slippage (for solver) based on provided inputs
                slippage = (
                    ((price := self.price(have, want)) - min_amount_out / amount_in) / price
                ).quantize(Decimal("1e-5"))

            return ExactInOrder(
                have=have,
                want=want,
                amount_in=amount_in,
                min_amount_out=min_amount_out,
                slippage=slippage,
            )

        else:
            raise ValueError("Must supply one of `amount_in=` or `amount_out=`.")

    def solve(
        self,
        order: Order | None = None,
        routes: Iterable[Route] | None = None,
        **order_kwargs,
    ) -> Solution:
        if not order:
            order = self.create_order(**order_kwargs)

        return self.solver(
            order,
            routes or list(self.find_routes(have=order.have, want=order.want)),
        )

    def create_plan(
        self,
        order: Order | None = None,
        routes: Iterable[Route] | None = None,
        permit_step: ur.Command | None = None,
        receiver: "str | BaseAddress | AddressType | None" = None,
        **order_kwargs,
    ) -> ur.Plan:
        if not order:
            order = self.create_order(**order_kwargs)

        if order.min_price > (market_price := self.price(order.have, order.want)):
            # NOTE: Give user some feedback but don't stop execution
            logger.warning(
                "Swap order might fail to solve or execute: "
                f"Min price '{order.min_price:0.6f}' higher than market price '{market_price:0.6f}'"
            )

        solution = self.solve(order=order, route=routes)

        if receiver is not None:
            receiver = self.conversion_manager.convert(receiver, AddressType)

        return convert_solution_to_plan(
            order,
            solution,
            permit_step=permit_step,
            receiver=receiver,
        )

    def approve_permit2(
        self,
        token: ConvertsToToken,
        allowance: Decimal | int | str = 2**256 - 1,
        **txn_kwargs,
    ) -> "ReceiptAPI":
        if not isinstance(token, TokenInstance):
            token = Token.at(self.conversion_manager.convert(token, AddressType))
        assert isinstance(token, TokenInstance)  # mypy happy

        if isinstance(allowance, Decimal):
            allowance = (
                2**256 - 1 if allowance.is_infinite() else int(allowance * 10 ** token.decimals())
            )

        return token.approve(self.permit2.contract, allowance, **txn_kwargs)

    def swap(
        self,
        order: Order | None = None,
        routes: Iterable[Route] | None = None,
        receiver: "str | BaseAddress | AddressType | None" = None,
        as_transaction: bool = False,
        deadline: timedelta | None = None,
        **order_and_txn_kwargs,
    ) -> "ReceiptAPI | TransactionAPI":
        order_kwargs: dict = dict()
        if not order:
            field: str  # NOTE: mypy happy
            for field in set(ExactInOrder.model_fields) | set(ExactOutOrder.model_fields):
                if field in order_and_txn_kwargs:
                    order_kwargs[field] = order_and_txn_kwargs.pop(field)

            order = self.create_order(**order_kwargs)

        have = order.have_token if order else order_and_txn_kwargs.get("have", order_kwargs["have"])
        if not isinstance(have, TokenInstance):
            have = Token.at(self.conversion_manager.convert(have, AddressType))

        from ape.api import AccountAPI

        permit_step = None
        if not isinstance(sender := order_and_txn_kwargs.get("sender"), AccountAPI):
            logger.warning("No `sender` present to sign permits with")

        elif have.allowance(sender, self.permit2.contract) < (
            amount := order.amount_in if isinstance(order, ExactInOrder) else order.max_amount_in
        ):
            logger.warning(
                f"Permit2 '{self.permit2.contract}' needs approval from '{sender}' "
                f"to spend at least {amount} {have.symbol()}."
            )

        else:
            expiration = datetime.now(timezone.utc) + (deadline or timedelta(minutes=2))
            permit_step = self.permit2.sign_permit(
                spender=self.router.contract,
                permit=PermitDetails(  # type: ignore[call-arg]
                    token=have.address,
                    amount=int(amount * 10 ** have.decimals()),
                    expiration=int(expiration.timestamp()),
                    nonce=self.permit2.get_nonce(sender, have, self.router.contract),
                ),
                signer=sender,
            )

        plan = self.create_plan(
            order=order,
            routes=routes,
            permit_step=permit_step,
            receiver=receiver,
            **order_kwargs,
        )

        if as_transaction:
            return self.router.plan_as_transaction(plan, deadline=deadline, **order_and_txn_kwargs)

        else:
            return self.router.execute(plan, deadline=deadline, **order_and_txn_kwargs)
