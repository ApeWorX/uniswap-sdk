import itertools
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Iterator

import networkx as nx  # type: ignore[import-untyped]
from ape.contracts import ContractInstance
from ape.logging import logger
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, cached_property
from ape_ethereum import multicall
from ape_tokens import Token, TokenInstance
from faster_eth_utils import to_int
from faster_eth_utils.address import to_checksum_address

from uniswap_sdk.utils import get_token_address, sort_tokens

from .packages import V2, get_contract_instance
from .types import BaseIndex, BasePair, ConvertsToToken, Fee, Route

if TYPE_CHECKING:
    from silverback import SilverbackBot


class Factory(BaseIndex):
    """
    Singleton class to interact with a deployment of the Uniswap V2 protocol's Factory contract.

    Usage example::

        >>> from uniswap_sdk import v2
        >>> factory = v2.Factory()
        >>> list(factory.index())  # NOTE: Run this or no pairs will be available for other methods
        >>> for pair in factory:
        ...     print(pair)  # NOTE: Only pulls pre-indexed tokens
        >>> len(factory)  # Cached, almost instantaneous
        396757
        >>> from ape_tokens import tokens
        >>> yfi = tokens["YFI"]
        >>> for pair in factory.get_pairs_by_token(yfi):
        ...     # Get all pairs that have `token` in it
        ...     print(pair)  # NOTE: Only pulls pre-indexed tokens
        >>> len(factory["YFI"])  # Already indexed, almost instantaneous
        3
        >>> usdc = tokens["USDC"]
        >>> for pair in factory.get_pairs(yfi, usdc):
        ...     # Will get all the available pair combos from `*tokens`
        ...     print(pair)  # NOTE: Only pulls pre-indexed tokens
        >>> pair = factory.get_pair(yfi, usdc)  # Single contract call
        <uniswap_sdk.v2.Pair address=0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d>

    """

    def __init__(self) -> None:
        # In-memory graph index of all pairs
        self._pair_by_address: dict[AddressType, "Pair"] = {}
        self._indexed_pairs = nx.Graph()
        self._last_indexed: int = 0

    @cached_property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)

    def __repr__(self) -> str:
        return f"<uniswap_sdk.v2.Factory address={self.contract.address}>"

    def get_pair(
        self,
        tokenA: ConvertsToToken,
        tokenB: ConvertsToToken,
    ) -> "Pair | None":
        token0, token1 = sort_tokens(
            (
                self.conversion_manager.convert(tokenA, AddressType),
                self.conversion_manager.convert(tokenB, AddressType),
            )
        )

        try:
            return self._indexed_pairs[token0][token1]["pair"]
        except KeyError:
            pass  # NOTE: Not indexed, go find it

        if (pair_address := self.contract.getPair(token0, token1)) == ZERO_ADDRESS:
            return None

        return Pair(address=pair_address, token0=token0, token1=token1)

    def get_pairs(
        self,
        *tokens: ConvertsToToken,
    ) -> Iterator["Pair"]:
        if len(tokens) < 2:
            raise ValueError("Must give at least two tokens to search for pairs")

        # TODO: Why does `ape_tokens` converter not return a checksummed address sometimes?
        converted_tokens = map(to_checksum_address, map(get_token_address, tokens))
        sorted_token_pairs = map(sort_tokens, itertools.combinations(converted_tokens, 2))

        calls = [multicall.Call()]
        token_pairs: list[tuple[AddressType, AddressType]] = []
        for token0, token1 in sorted_token_pairs:
            try:
                yield self._indexed_pairs[token0][token1]["pair"]

            except KeyError:
                # NOTE: Not indexed, go find it via multicall instead
                token_pairs.append((token0, token1))
                calls[-1].add(self.contract.getPair, token0, token1)

                if len(calls[-1].calls) >= 10_000:
                    calls.append(multicall.Call())

        pair_addresses = itertools.chain(call() for call in calls)
        for pair_address, (token0, token1) in zip(*pair_addresses, token_pairs):
            if pair_address != ZERO_ADDRESS:
                pair = Pair(address=pair_address, token0=token0, token1=token1)
                self._indexed_pairs.add_edge(token0, token1, pair=pair)
                self._pair_by_address[pair.address] = pair
                yield pair

    def __len__(self) -> int:
        return self.contract.allPairsLength()

    def index(
        self,
        tokens: Iterable[ConvertsToToken] | None = None,
    ) -> Iterator["Pair"]:
        logger.info("Uniswap v2 - indexing")
        num_pairs = 0
        if tokens:
            for pair in self.get_pairs(*tokens):
                yield pair
                num_pairs += 1
            logger.success(f"Uniswap v2 - indexed {num_pairs} pairs")
            return  # NOTE: Shortcut for indexing less

        # TODO: Reformat to use query system when better (using PairCreated)
        #       and potentially move this to a query engine implementation
        pair_calls = [multicall.Call()]
        for idx in range(self._last_indexed, num_pairs := self.__len__()):
            pair_calls[-1].add(self.contract.allPairs, idx)
            if len(pair_calls[-1].calls) >= 10_000:
                pair_calls.append(multicall.Call())

        pair_addresses = []
        token_calls = [multicall.Call()]
        for pair_address in itertools.chain(call() for call in pair_calls):
            pair_addresses.append(pair_address)

            token_calls[-1].calls.append(
                dict(
                    target=pair_address,
                    value=0,
                    allowFailure=False,
                    callData=bytes.fromhex("0dfe1681"),  # methodID for `token0()`
                )
            )
            token_calls[-1].abis.append(V2.UniswapV2Pair.contract_type.view_methods["token0"])

            token_calls[-1].calls.append(
                dict(
                    target=pair_address,
                    value=0,
                    allowFailure=False,
                    callData=bytes.fromhex("d21220a7"),  # methodID for `token1()`
                )
            )
            token_calls[-1].abis.append(V2.UniswapV2Pair.contract_type.view_methods["token1"])

            if len(token_calls[-1].calls) >= 10_000:
                token_calls.append(multicall.Call())

        def yield_pairs(itr):
            # TODO: `itertools.batched` added in 3.12, backport?
            try:
                yield next(itr), next(itr)
            except StopIteration:
                pass

        for pair_address, (token0, token1) in zip(
            pair_addresses,
            yield_pairs(itertools.chain(call() for call in token_calls)),
        ):
            pair = Pair(address=pair_address, token0=token0, token1=token1)
            self._indexed_pairs.add_edge(token0, token1, pair=pair)
            self._pair_by_address[pair.address] = pair
            yield pair
            num_pairs += 1

        logger.success(f"Uniswap v2 - indexed {num_pairs} pairs")
        self._last_indexed = num_pairs  # Skip indexing loop next time from this height

    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[ConvertsToToken] | None = None,
    ):
        from silverback.types import TaskType

        async def index_existing_pairs(snapshot):
            for pair in self.index(tokens=tokens):
                pair.liquidity = _ManagedLiquidity(pair)

        # NOTE: Modify name to namespace it from user tasks
        index_existing_pairs.__name__ = f"uniswap:v2:{index_existing_pairs.__name__}"
        bot.broker_task_decorator(TaskType.STARTUP)(index_existing_pairs)

        async def index_new_pair(log):
            if log.token0 in self._indexed_pairs or log.token1 in self._indexed_pairs:
                pair = Pair(address=log.pair, token0=log.token0, token1=log.token1)
                pair.liquidity = _ManagedLiquidity(pair)
                self._indexed_pairs.add_edge(log.token0, log.token1, pair=pair)

        # NOTE: Modify name to namespace if from user tasks
        index_new_pair.__name__ = f"uniswap:v2:{index_new_pair.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=self.contract.PairCreated)(
            index_new_pair
        )

        async def sync_pair_liquidity(log):
            if pair := self._pair_by_address.get(log.contract_address):
                assert isinstance(pair.liquidity, _ManagedLiquidity)  # mypy happy
                pair.liquidity.reserve0 = log.reserve0
                pair.liquidity.reserve1 = log.reserve1
                pair.liquidity.last_updated = log.block.timestamp

        # NOTE: Modify name to namespace if from user tasks
        sync_pair_liquidity.__name__ = f"uniswap:v2:{sync_pair_liquidity.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=V2.UniswapV2Pair.Sync)(
            sync_pair_liquidity
        )

    def __iter__(self) -> Iterator["Pair"]:
        # Yield pairs from all edges in index
        yield from self._pair_by_address.values()

    def get_pairs_by_token(self, token: ConvertsToToken) -> Iterator["Pair"]:
        token = self.conversion_manager.convert(token, AddressType)

        # Yield pair from edges that have token as node in index
        for edge in self._indexed_pairs[token].values():
            yield edge["pair"]

    def __getitem__(self, token: ConvertsToToken) -> list["Pair"]:
        return list(self.get_pairs_by_token(token))

    def find_routes(
        self,
        start_token: ConvertsToToken,
        end_token: ConvertsToToken,
        depth: int = 2,
    ) -> Iterator[Route["Pair"]]:
        start_token = self.conversion_manager.convert(start_token, AddressType)
        end_token = self.conversion_manager.convert(end_token, AddressType)

        try:
            for edge_paths in nx.all_simple_edge_paths(
                self._indexed_pairs, start_token, end_token, cutoff=depth
            ):
                yield tuple(self._indexed_pairs[u][v]["pair"] for u, v in edge_paths)

        except nx.NodeNotFound as e:
            raise KeyError(f"Cannot solve: {start_token} or {end_token} is not indexed.") from e

    @classmethod
    def encode_route(cls, token: TokenInstance, *route: "Pair") -> tuple[AddressType, ...]:
        encoded_path = [token.address]

        for pair in route:
            token = pair.other(token)
            encoded_path.append(token.address)

        return tuple(encoded_path)


class Pair(BasePair):
    """
    Represents a UniswapV2Pair contract, which implements swaps between two tokens
    according to the x*y=k constant product market maker function

    Usage example::

        >>> from uniswap_sdk import v2
        >>> pair = v2.Pair(address="0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d")
        >>> pair.liquidity["YFI"]  # Get reserves of token in pair (in appropiate decimals)
        Decimal('0.000010265...')
        >>> print(f"Price is {pair.price('YFI'):0,.2f} [YFI/{pair.other('YFI').symbol()}]")
        Price is 2,196.81 [YFI/USDC]

    """

    fee: Fee = Fee.MEDIUM  # v2 has a static fee

    def __init__(
        self,
        address: AddressType,
        token0: TokenInstance | AddressType | None = None,
        token1: TokenInstance | AddressType | None = None,
    ):

        self.address = address
        # NOTE: `None` is not supported by `BasePair`, but we override below
        super().__init__(token0=token0, token1=token1)

    def __hash__(self) -> int:
        return to_int(hexstr=self.address)

    def __eq__(self, other) -> bool:
        return isinstance(other, Pair) and self.address == other.address

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__module__}.{self.__class__.__name__} "
            f"address={self.address} "
            f"pair='{self.token0.symbol()}/{self.token1.symbol()}'>"
        )

    @cached_property
    def contract(self) -> ContractInstance:
        # TODO: Make ContractInstance.at cache?
        #       Dunno what causes all the `eth_chainId` requests over and over
        return V2.UniswapV2Pair.at(self.address)

    @cached_property
    def token0(self) -> TokenInstance:
        return Token.at(self._token0_address or self.contract.token0())

    @cached_property
    def token1(self) -> TokenInstance:
        return Token.at(self._token1_address or self.contract.token1())

    @cached_property
    # NOTE: Use `cached_property` so it can be overriden for a managed scenario (e.g. Silverback)
    def liquidity(self) -> "Liquidity":
        # Default is live RPC query
        return Liquidity(pair=self)

    def price(self, token: ConvertsToToken, block_id: int | str = "latest") -> Decimal:
        token0_reserve, token1_reserve, _ = self.liquidity.get_reserves(block_id=block_id)
        token0_balance = Decimal(token0_reserve) / Decimal(10 ** self.token0.decimals())
        token1_balance = Decimal(token1_reserve) / Decimal(10 ** self.token1.decimals())

        if token0_balance.is_zero() or token1_balance.is_zero():
            raise ValueError("Pair uninitialized")

        elif self.is_token0(token):
            return token1_balance / token0_balance

        elif self.is_token1(token):
            return token0_balance / token1_balance

        else:
            raise ValueError(f"Token {token} not in pair")

    def depth(self, token: ConvertsToToken, slippage: Decimal | float | str) -> Decimal:
        if not isinstance(slippage, Decimal):
            slippage = Decimal(slippage)

        if not (0 < slippage < 1):
            raise ValueError(f"Slippage out of bounds: {slippage}. Must be a ratio in (0, 1).")

        # NOTE: Slippage is defined as being a nonzero ratio, however formula expects negative
        return (self.liquidity[token] / (1 - self.fee.to_decimal())) * (
            (1 / (1 - slippage).sqrt()) - 1
        )

    def reflexivity(self, token: ConvertsToToken, size: Decimal | int | str) -> Decimal:
        if not isinstance(size, Decimal):
            size = Decimal(self.conversion_manager.convert(size, int)) / 10 ** Decimal(
                self.token0.decimals() if self.is_token0(token) else self.token1.decimals()
            )

        liquidity = self.liquidity[token]

        if not (0 < size < liquidity):
            raise ValueError(f"Size out of bounds: {size}. Must be nonzero and below {liquidity}.")

        return 1 - (liquidity / (liquidity + (1 - self.fee.to_decimal()) * size)) ** 2


class Liquidity:
    """Using a method (either managed or live query), fetch liquidity info for v2.Pair"""

    def __init__(self, pair: Pair):
        self._pair = pair

    def get_reserves(self, block_id: int | str = "latest") -> tuple[int, int, int]:
        if isinstance(block_id, int) and block_id < 0:
            block_id += self._pair.chain_manager.blocks.head.number
        return self._pair.contract.getReserves(block_id=block_id)

    def __getitem__(self, token: ConvertsToToken) -> Decimal:
        if self._pair.is_token0(token):
            return Decimal(self.get_reserves()[0]) / Decimal(10 ** self._pair.token0.decimals())

        elif self._pair.is_token1(token):
            return Decimal(self.get_reserves()[1]) / Decimal(10 ** self._pair.token1.decimals())

        else:
            raise ValueError(f"Token {token} not in pair {self._pair.address}")


class _ManagedLiquidity(Liquidity):
    """Cached liquidity information for v2.Pair via Silverback live-indexing"""

    def __init__(self, pair: Pair):
        self._pair = pair
        self.reserve0, self.reserve1, self.last_updated = pair.contract.getReserves()

    def get_reserves(self, block_id: int | str = "latest") -> tuple[int, int, int]:
        if block_id != "latest":
            block_id += self._pair.chain_manager.blocks.head.number
            return super().get_reserves(block_id=block_id)

        # else: return managed cache value
        return self.reserve0, self.reserve1, self.last_updated
