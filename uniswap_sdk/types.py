from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, Iterable, Iterator, TypeVar

from ape.types import AddressType
from ape.utils import ManagerAccessMixin, cached_property
from ape_tokens import Token, TokenInstance
from eth_utils import is_checksum_address
from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from ape.api import BaseAddress
    from silverback import SilverbackBot

ConvertsToToken = TypeVar("ConvertsToToken", bound="BaseAddress | AddressType | str")
PairType = TypeVar("PairType", bound="BasePair")
Route = tuple[PairType, ...]


class BaseOrder(BaseModel, ManagerAccessMixin):
    have: AddressType
    want: AddressType
    slippage: Decimal

    def __init__(self, **model_kwargs):
        if isinstance(have_token := model_kwargs["have"], TokenInstance):
            self.have_token = have_token
            model_kwargs["have"] = have_token.address

        if isinstance(want_token := model_kwargs["want"], TokenInstance):
            self.want_token = want_token
            model_kwargs["want"] = want_token.address

        super().__init__(**model_kwargs)

    def describe(self) -> str:
        raise NotImplementedError

    @cached_property
    def have_token(self) -> TokenInstance:
        return Token.at(self.have)

    @cached_property
    def want_token(self) -> TokenInstance:
        return Token.at(self.want)

    @property
    def min_price(self) -> Decimal:
        raise NotImplementedError


class ExactInOrder(BaseOrder):
    amount_in: Decimal = Field(gt=Decimal(0))
    min_amount_out: Decimal = Field(gt=Decimal(0))

    def describe(self) -> str:
        have_symbol = self.have_token.symbol()
        want_symbol = self.want_token.symbol()
        return (
            f"Swap {self.amount_in:0.3f} {have_symbol} to "
            f"at least {self.min_amount_out:0.3f} {want_symbol} "
            f"@ {self.min_price:0.5f} {want_symbol}/{have_symbol}"
        )

    @model_validator(mode="after")
    def truncate_amounts(self):
        self.amount_in = self.amount_in.quantize(Decimal(f"1e-{self.have_token.decimals()}"))
        self.min_amount_out = self.min_amount_out.quantize(
            Decimal(f"1e-{self.want_token.decimals()}")
        )
        return self

    @property
    def min_price(self) -> Decimal:
        return self.min_amount_out / self.amount_in


class ExactOutOrder(BaseOrder):
    max_amount_in: Decimal = Field(gt=Decimal(0))
    amount_out: Decimal = Field(gt=Decimal(0))

    def describe(self) -> str:
        have_symbol = self.have_token.symbol()
        want_symbol = self.want_token.symbol()
        return (
            f"Swap at most {self.max_amount_in:0.3f} {have_symbol} to "
            f"{self.amount_out:0.3f} {want_symbol} "
            f"@ {self.min_price:0.5f} {want_symbol}/{have_symbol}"
        )

    @model_validator(mode="after")
    def truncate_amounts(self):
        self.max_amount_in = self.max_amount_in.quantize(
            Decimal(f"1e-{self.have_token.decimals()}")
        )
        self.amount_out = self.amount_out.quantize(Decimal(f"1e-{self.want_token.decimals()}"))
        return self

    @property
    def min_price(self) -> Decimal:
        return self.amount_out / self.max_amount_in


Order = ExactInOrder | ExactOutOrder


class BaseIndex(ABC, ManagerAccessMixin, Generic[PairType]):
    @abstractmethod
    def index(
        self,
        tokens: Iterable[ConvertsToToken] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator[PairType]: ...

    @abstractmethod
    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[ConvertsToToken] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ): ...

    @abstractmethod
    def __getitem__(self, token: ConvertsToToken) -> list[PairType]: ...

    # TODO: others?
    # `.get(tokenA, tokenB) -> BasePair | None` (more generic form of `.get_pair`)
    # `.get_matches(*tokens) -> iter[BasePair]` (more generic form of `.get_pairs`)
    # `.get_all() -> iter[BasePair]` (more generic form of `.get_all_pairs`)

    @abstractmethod
    def find_routes(
        self,
        start_token: ConvertsToToken,
        end_token: ConvertsToToken,
        depth: int = 2,
    ) -> Iterator[Route]:
        """
        Find all routes (sequence of pairs) between ``start_token`` to ``end_token``,
        up to ``depth`` in length.

        ```{notice}
        Using depth greater than 2 takes a long long time, unless the index size is small
        ```

        ```{notice}
        Method will return longest routes first, as it performs exhaustive DFS.
        It may preferrable to use ``reversed`` to flip it to obtain shortest routes.
        ```
        """

    @classmethod
    @abstractmethod
    def encode_route(cls, token: TokenInstance, *route: PairType) -> tuple[Any, ...]:
        """Convert ``route`` into a UniversalRouter-accepted encoded path."""


class BaseLiquidity(ABC):
    """Object that represents a pair's reserves"""

    @abstractmethod
    def __getitem__(self, token: ConvertsToToken) -> Decimal:
        """
        Maxmimum amount of token that can be swapped via pair.
        At most, this should be similar to ``token.balanceOf(pair)``.
        For pair types that have piecewise liquidity,
        this can sum the available liquidity in the "buy" direction.
        """


class Fee(int, Enum):
    # From Uniswap V3 SDK
    LOWEST = 100  # 1 bip
    LOW_200 = 200
    LOW_300 = 300
    LOW_400 = 400
    LOW = 500  # 0.05%
    MEDIUM = 3_000  # 0.3%
    HIGH = 10_000  # 1.0%

    MAXIMUM = 1_000_000  # 100%

    @property
    def tick_spacing(self) -> int:
        return {
            Fee.LOWEST: 1,
            Fee.LOW_200: 4,
            Fee.LOW_300: 6,
            Fee.LOW_400: 8,
            Fee.LOW: 10,
            Fee.MEDIUM: 60,
            Fee.HIGH: 200,
        }[self]

    def to_decimal(self) -> Decimal:
        # Convert to ratio in decimal (for fee math)
        return self.value / Decimal(10**6)


class BasePair(ABC, ManagerAccessMixin):
    fee: Fee

    def __init__(
        self,
        token0: ConvertsToToken,
        token1: ConvertsToToken,
    ):
        # Cache if available
        if isinstance(token0, TokenInstance):
            # NOTE: Completely overrides value of `cached_property`
            self.token0 = token0

        else:
            self._token0_address = self.conversion_manager.convert(token0, AddressType)

        if isinstance(token1, TokenInstance):
            # NOTE: Completely overrides value of `cached_property`
            self.token1 = token1

        else:
            self._token1_address = self.conversion_manager.convert(token1, AddressType)

    def describe(self) -> str:
        return (
            f"V{self.__class__.__module__[-1]} "
            f"{self.token0.symbol()}/{self.token1.symbol()} "
            f"{self.fee.to_decimal() * 100:0.5f}%"
        )

    @property
    def key(self) -> int:
        # NOTE: For V2, there is only 1 possible pair
        # NOTE: For V3, there are multiple pools per pair, keyed by fee
        # TODO: Override for V4, since pools are paired by PoolKey instead of fee
        return int(self.fee)

    @cached_property
    def token0(self) -> TokenInstance:
        return Token.at(self._token0_address)

    @cached_property
    def token1(self) -> TokenInstance:
        return Token.at(self._token1_address)

    # NOTE: Required for solving
    @abstractmethod
    def __hash__(self) -> int: ...

    # NOTE: Required for solving
    @abstractmethod
    def __eq__(self, other: Any) -> bool: ...

    @abstractmethod
    def __repr__(self) -> str: ...

    def is_token0(self, token: ConvertsToToken) -> bool:
        if isinstance(token, str) and not is_checksum_address(token):
            return self.token0.symbol() == token

        return self.token0.address == self.conversion_manager.convert(token, AddressType)

    def is_token1(self, token: ConvertsToToken) -> bool:
        if isinstance(token, str) and not is_checksum_address(token):
            return self.token1.symbol() == token

        return self.token1.address == self.conversion_manager.convert(token, AddressType)

    def __contains__(self, token: ConvertsToToken) -> bool:
        return self.is_token0(token) or self.is_token1(token)

    def other(self, token: ConvertsToToken) -> TokenInstance:
        if self.is_token0(token):
            return self.token1

        elif self.is_token1(token):
            return self.token0

        raise ValueError(f"Token {token} is not in {self}")

    @abstractmethod
    def price(
        self,
        token: ConvertsToToken,
        block_id: int | str = "latest",
    ) -> Decimal:
        """
        Price of ``token`` relative to the other token in the pair. Can be performed at the
        latest block, or at the block given by ``block_id``. Used for routing and solving.
        """

    @property
    @abstractmethod
    def liquidity(self) -> "BaseLiquidity":
        """
        Return an object that is capable of supplying liquidity information. Used for routing.
        """

    @abstractmethod
    def depth(self, token: ConvertsToToken, price_change: Decimal) -> Decimal:
        """
        Maximum amount of `token` that can be swapped that keeps ``.price(token)`` below
        ``price_change`` (a ratio between 0 and 1, e.g. 5% is 0.05). Required for solving.
        """

    @abstractmethod
    def reflexivity(self, token: ConvertsToToken, size: Decimal) -> Decimal:
        """
        The relative change in ``.price(token)`` after swapping ``size`` of ``token`` to
        ``.other(token)``. Is unitless. Required for solving.
        """
