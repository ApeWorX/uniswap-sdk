from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable, Iterator, TypeVar

from ape.contracts import ContractInstance
from ape.types import AddressType
from ape.utils import cached_property
from ape_tokens import Token, TokenInstance
from eth_utils import is_checksum_address

if TYPE_CHECKING:
    from silverback import SilverbackBot

PairType = TypeVar("PairType", bound="BasePair")
Route = tuple[PairType, ...]


class BaseIndex(ABC):
    @abstractmethod
    def index(
        self,
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator["BasePair"]: ...

    @abstractmethod
    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ): ...

    @abstractmethod
    def __getitem__(self, token: TokenInstance | AddressType) -> list["BasePair"]: ...

    # TODO: others?
    # `.get(tokenA, tokenB) -> BasePair | None` (more generic form of `.get_pair`)
    # `.get_matches(*tokens) -> iter[BasePair]` (more generic form of `.get_pairs`)
    # `.get_all() -> iter[BasePair]` (more generic form of `.get_all_pairs`)

    # TODO: other search methods?
    # TODO: `.price(tokenA, tokenB) -> Decimal`
    # TODO: `.depth(tokenA, tokenB) -> Decimal`
    # TODO: `.solve(size, tokenA, tokenB) -> Iterator[Route]`

    @abstractmethod
    def find_routes(
        self,
        start_token: TokenInstance | AddressType,
        end_token: TokenInstance | AddressType,
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


class BaseLiquidity(ABC):
    """Object that represents a pair's reserves"""

    @abstractmethod
    def __getitem__(self, token: TokenInstance | AddressType) -> Decimal:
        """
        Maxmimum amount of token that can be swapped via pair.
        At most, this should be similar to ``token.balanceOf(pair)``.
        For pair types that have piecewise liquidity,
        this can sum the available liquidity in the "buy" direction.
        """


class BasePair(ABC):
    def __init__(
        self,
        token0: TokenInstance | AddressType,
        token1: TokenInstance | AddressType,
    ):
        # Cache if available
        if isinstance(token0, ContractInstance):
            # NOTE: Completely overrides value of `cached_property`
            self.token0 = token0
        else:
            self._token0_address = token0

        if isinstance(token1, ContractInstance):
            # NOTE: Completely overrides value of `cached_property`
            self.token1 = token1
        else:
            self._token1_address = token1

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

    def is_token0(self, token: ContractInstance | str) -> bool:
        if isinstance(token, str) and not is_checksum_address(token):
            return self.token0.symbol() == token
        else:
            return self.token0 == token

    def is_token1(self, token: ContractInstance | str) -> bool:
        if isinstance(token, str) and not is_checksum_address(token):
            return self.token1.symbol() == token
        else:
            return self.token1 == token

    def other(self, token: ContractInstance | str) -> TokenInstance:
        if self.is_token0(token):
            return self.token1

        elif self.is_token1(token):
            return self.token0

        raise ValueError(f"Token {token} is not one of the tokens in pool")

    @abstractmethod
    def price(
        self,
        token: ContractInstance | str,
        block_id: int | str = "latest",
    ) -> Decimal:
        """
        Price of ``token`` relative to the other token in the pair.
        Can be performed at the latest block, or at the block given by ``block_id``.
        """

    @property
    @abstractmethod
    def liquidity(self) -> "BaseLiquidity":
        """
        Return an object that is capable of supplying liquidity information
        """
