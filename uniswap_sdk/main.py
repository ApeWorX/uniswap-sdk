from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Union

from ape.api import ReceiptAPI
from ape.contracts import ContractInstance
from ape.types import AddressType
from ape.utils import ManagerAccessMixin

from . import universal_router as ur
from . import v2  # TODO: v3, v4

try:
    from ape_tokens import ERC20
except ImportError:
    ERC20 = None

if TYPE_CHECKING:
    from silverback import SilverbackBot

DEFAULT_SLIPPAGE = Decimal("0.005")  # 0.5%


class Uniswap(ManagerAccessMixin):
    """
    Main class used to work with all Uniswap protocol deployments on current chain for swapping,
    pricing, indexing, solving, and more.

    Example usage::

        >>> from uniswap_sdk import Uniswap
        >>> uni = Uniswap()
        >>> uni.index()  # Takes a long time, but makes planning faster
        >>> from ape_tokens import tokens
        >>> yfi = tokens["YFI"]
        >>> usdc = token["USDC"]
        >>> uni.swap(
        ...     yfi,
        ...     usdc,
        ...     amount_in="12 YFI",
        ...     slippage=0.3,
        ...     deadline=timedelta(minutes=2),
        ...     sender=trader,
        ... )

    """

    def __init__(self):
        self.v2 = v2.Factory()
        self.router = ur.UniversalRouter()

    def index(self):
        """Index all factory/singleton deployments (takes time)"""
        self.v2._index_all_pairs()

    def install(self, bot: "SilverbackBot"):
        """
        Install onto Silverback bot instance to integrate real-time indexing.

        Args:
            bot (:class:`~silverback.SilverbackBot`):
                The bot instance to use to handle real-time updates.

        Example usage::

                >>> from ape import chain
                >>> from silverback import SilverbackBot
                >>> from uniswap_sdk import Uniswap
                >>> bot = SilverbackBot()
                >>> uni = Uniswap()
                >>> uni.install(bot)
                >>> # `bot` will process real-time updates of protocol indexes
                >>> # NOTE: also will index all tokens on bot startup (takes time)
                >>> @bot.on_(chain.blocks)
                ... async def price(blk):
                ...     # Call at any time using up-to-date protocol info
                ...     return uni.price("YFI", "USDC")

        """
        self.v2.install(bot)

    def create_plan(
        self,
        input_token: Union[ContractInstance, AddressType, str],
        output_token: Union[ContractInstance, AddressType, str],
        input_amount: Union[str, int, None] = None,
        output_amount: Union[str, int, None] = None,
        min_price: Optional[Decimal] = None,
        min_liquidity: Union[str, int] = 0,
        slippage: Decimal = DEFAULT_SLIPPAGE,
        receiver: AddressType = ur.Constants.MSG_SENDER,
        payer_is_user: bool = True,
    ) -> ur.Plan:
        """Create a swap plan for the Universal Router."""
        if not isinstance(input_token, ContractInstance):
            input_token = self.chain_manager.contracts.instance_at(
                input_token,
                contract_type=ERC20,
            )

        if not isinstance(input_token, ContractInstance):
            output_token = self.chain_manager.contracts.instance_at(
                output_token,
                contract_type=ERC20,
            )

        amount_in: Optional[Decimal] = (
            Decimal(self.conversion_manager.convert(input_amount, int))
            / Decimal(10 ** input_token.decimals())
            if input_amount is not None
            else input_amount
        )

        amount_out: Optional[Decimal] = (
            Decimal(self.conversion_manager.convert(output_amount, int))
            / Decimal(10 ** output_token.decimals())
            if output_amount is not None
            else output_amount
        )

        if not min_price:
            min_price = self.price(input_token, output_token) * Decimal(1 - slippage)

        plan = ur.Plan()

        for path, amount in self.v2.solve_best_path_flow(
            tokenA=input_token.address,
            tokenB=output_token.address,
            amount_in=amount_in,
            amount_out=amount_out,
            min_liquidity=self.conversion_manager.convert(min_liquidity, int)
            / Decimal(10 ** input_token.decimals()),
        ):
            plan = plan.v2_swap_exact_in(
                receiver,
                int(amount * (10 ** input_token.decimals())),
                int((amount / min_price) * (10 ** output_token.decimals())),
                path,
                payer_is_user,
            )

        return plan

    def swap(
        self,
        input_token: Union[ContractInstance, AddressType, str],
        output_token: Union[ContractInstance, AddressType, str],
        input_amount: Union[str, int, None] = None,
        output_amount: Union[str, int, None] = None,
        min_price: Optional[Decimal] = None,
        min_liquidity: Union[str, int] = 0,
        slippage: Decimal = DEFAULT_SLIPPAGE,
        receiver: AddressType = ur.Constants.MSG_SENDER,
        payer_is_user: bool = True,
        deadline: Union[timedelta, int, None] = None,
        **txn_args,
    ) -> ReceiptAPI:
        """Create and perform swap plan using Universal Router."""
        plan = self.create_plan(
            input_token=input_token,
            output_token=output_token,
            input_amount=input_amount,
            output_amount=output_amount,
            min_price=min_price,
            min_liquidity=min_liquidity,
            slippage=slippage,
            receiver=receiver,
            payer_is_user=payer_is_user,
        )

        return self.router.execute(plan, deadline=deadline, **txn_args)

    def price(
        self,
        input_token: Union[ContractInstance, AddressType, str],
        output_token: Union[ContractInstance, AddressType, str],
    ) -> Decimal:
        """Obtain latest price using up-to-date liquidity info."""
        try:
            v2_price, v2_liquidity = self.v2.all_route_price_liquidity(
                self.conversion_manager.convert(input_token, AddressType),
                self.conversion_manager.convert(output_token, AddressType),
            )

        except ValueError:
            v2_price, v2_liquidity = [], []

        # TODO: Add V3

        route_price = [*v2_price]
        route_liquidity = [*v2_liquidity]

        if not (len(route_price) == len(route_liquidity) > 0):
            raise AssertionError("Invariant violation")

        return Decimal(
            sum(liquidity * price for liquidity, price in zip(route_liquidity, route_price))
        ) / Decimal(sum(route_liquidity))
