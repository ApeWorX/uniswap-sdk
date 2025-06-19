import itertools
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Iterator

import networkx as nx  # type: ignore[import-untyped]
from ape.contracts import ContractInstance
from ape.logging import logger
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, ManagerAccessMixin, cached_property
from ape_ethereum import multicall
from ape_tokens import Token, TokenInstance
from eth_utils import to_int

from .packages import V2, get_contract_instance
from .types import BaseIndex, BasePair, Route
from .utils import get_token_address, sort_tokens

if TYPE_CHECKING:
    from silverback import SilverbackBot


class Factory(ManagerAccessMixin, BaseIndex):
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
        tokenA: TokenInstance | AddressType,
        tokenB: TokenInstance | AddressType,
    ) -> "Pair | None":
        if isinstance(tokenA, TokenInstance):
            tokenA = tokenA.address
        if isinstance(tokenB, TokenInstance):
            tokenB = tokenB.address

        try:
            return self._indexed_pairs[tokenA][tokenB]["pair"]
        except KeyError:
            pass  # NOTE: Not indexed, go find it

        if (pair_address := self.contract.getPair(tokenA, tokenB)) == ZERO_ADDRESS:
            return None

        if get_token_address(tokenA) < get_token_address(tokenB):
            return Pair(address=pair_address, token0=tokenA, token1=tokenB)
        else:
            return Pair(address=pair_address, token0=tokenB, token1=tokenA)

    def get_pairs(
        self,
        *tokens: TokenInstance | AddressType,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator["Pair"]:
        if len(tokens) < 2:
            raise ValueError("Must give at least two tokens to search for pairs")

        ordered_token_pairs, token_pairs = itertools.tee(itertools.combinations(tokens, 2))

        ordered_token_pairs = map(sort_tokens, ordered_token_pairs)

        calls = [multicall.Call()]
        for tokenA, tokenB in token_pairs:
            addr_a = get_token_address(tokenA)
            addr_b = get_token_address(tokenB)
            try:
                yield self._indexed_pairs[addr_a][addr_b]["pair"]
            except KeyError:
                # NOTE: Not indexed, go find it via multicall instead
                calls[-1].add(self.contract.getPair, tokenA, tokenB)

            if len(calls[-1].calls) >= 10_000:
                calls.append(multicall.Call())

        pair_addresses = itertools.chain(call() for call in calls)
        for pair_address, (token0, token1) in zip(*pair_addresses, ordered_token_pairs):
            if pair_address != ZERO_ADDRESS:
                pair = Pair(address=pair_address, token0=token0, token1=token1)
                if pair.liquidity[token0] < min_liquidity:
                    continue

                self._indexed_pairs.add_edge(pair.token0.address, pair.token1.address, pair=pair)
                self._pair_by_address[pair.address] = pair
                yield pair

    def __len__(self) -> int:
        return self.contract.allPairsLength()

    def index(
        self,
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator["Pair"]:
        logger.info("Uniswap v2 - indexing")
        num_pairs = 0
        if tokens:
            for pair in self.get_pairs(*tokens, min_liquidity=min_liquidity):
                yield pair
                num_pairs += 1
            logger.success(f"Uniswap v2 - indexed {num_pairs} pairs")
            return  # NOTE: Shortcut for indexing less

        # TODO: Reformat to query system when better (using PairCreated)
        #       and move this to a query engine?
        pair_calls = [multicall.Call()]
        for idx in range(self._last_indexed, num_pairs := self.__len__()):
            pair_calls[-1].add(self.contract.allPairs, idx)
            if len(pair_calls[-1].calls) >= 10_000:
                pair_calls.append(multicall.Call())

        nonzero_pairs, pair_addresses = itertools.tee(
            filter(
                lambda a: a != ZERO_ADDRESS,
                itertools.chain(call() for call in pair_calls),
            )
        )

        token_calls = [multicall.Call()]
        for pair_address in nonzero_pairs:
            token_calls[-1].calls.append(
                dict(
                    target=pair_address,
                    value=0,
                    allowFailure=False,
                    callData=bytes.fromhex("0dfe1681"),
                )
            )
            token_calls[-1].abis.append(V2.UniswapV2Pair.contract_type.view_methods["token0"])

            token_calls[-1].calls.append(
                dict(
                    target=pair_address,
                    value=0,
                    allowFailure=False,
                    callData=bytes.fromhex("d21220a7"),
                )
            )
            token_calls[-1].abis.append(V2.UniswapV2Pair.contract_type.view_methods["token1"])

            if len(token_calls[-1].calls) >= 10_000:
                token_calls.append(multicall.Call())

        def yield_pairs(itr):
            # TODO: Is there a built-in solution?
            try:
                yield next(itr), next(itr)
            except StopIteration:
                pass

        for pair_address, (token0, token1) in zip(
            *pair_addresses,
            yield_pairs(itertools.chain(call() for call in token_calls)),
        ):
            pair = Pair(address=pair_address, token0=token0, token1=token1)
            if pair.liquidity[token0] >= min_liquidity:
                self._indexed_pairs.add_edge(token0, token1, pair=pair)
                self._pair_by_address[pair.address] = pair
                yield pair
                num_pairs += 1

        logger.success(f"Uniswap v2 - indexed {num_pairs} pairs")
        self._last_indexed = num_pairs  # Skip indexing loop next time from this height

    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ):
        from silverback.types import TaskType

        async def index_existing_pairs(snapshot):
            for pair in self.index(tokens=tokens, min_liquidity=min_liquidity):
                pair.liquidity = _ManagedLiquidity(pair)

        # NOTE: Modify name to namespace it from user tasks
        index_existing_pairs.__name__ = f"uniswap:v2:{index_existing_pairs.__name__}"
        bot.broker_task_decorator(TaskType.STARTUP)(index_existing_pairs)

        async def index_new_pair(log):
            if log.token0 in self._indexed_pairs or log.token1 in self._indexed_pairs:
                pair = Pair(address=log.pair, token0=log.token0, token1=log.token1)
                if pair.liquidity[log.token0] >= min_liquidity:
                    pair.liquidity = _ManagedLiquidity(pair)
                    self._indexed_pairs.add_edge(log.token0, log.token1, pair=pair)

        # NOTE: Modify name to namespace it from user tasks
        index_new_pair.__name__ = f"uniswap:v2:{index_new_pair.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=self.contract.PairCreated)(
            index_new_pair
        )

        async def sync_pair_liquidity(log):
            if pair := self._pair_by_address.get(log.contract_address):
                assert isinstance(pair.liquidity, _ManagedLiquidity)
                pair.liquidity.reserve0 = log.reserve0
                pair.liquidity.reserve1 = log.reserve1
                pair.liquidity.last_updated = log.block.timestamp

        # NOTE: Modify name to namespace it from user tasks
        sync_pair_liquidity.__name__ = f"uniswap:v2:{sync_pair_liquidity.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=V2.UniswapV2Pair.Sync)(
            sync_pair_liquidity
        )

    def __iter__(self) -> Iterator["Pair"]:
        # Yield pairs from all edges in index
        yield from self._pair_by_address.values()

    def get_pairs_by_token(self, token: TokenInstance | AddressType) -> Iterator["Pair"]:
        if isinstance(token, TokenInstance):
            token = token.address

        # Yield pair from edges that have token as node in index
        for edge in self._indexed_pairs[token].values():
            yield edge["pair"]

    def __getitem__(self, token: TokenInstance | AddressType) -> list[BasePair]:
        return list(self.get_pairs_by_token(token))

    def find_routes(
        self,
        start_token: TokenInstance | AddressType,
        end_token: TokenInstance | AddressType,
        depth: int = 2,
    ) -> Iterator[Route["Pair"]]:
        if isinstance(start_token, TokenInstance):
            start_token = start_token.address

        if isinstance(end_token, TokenInstance):
            end_token = end_token.address

        try:
            for edge_paths in nx.all_simple_edge_paths(
                self._indexed_pairs, start_token, end_token, cutoff=depth
            ):
                yield tuple(self._indexed_pairs[u][v]["pair"] for u, v in edge_paths)

        except nx.NodeNotFound as e:
            raise KeyError(f"Cannot solve: {start_token} or {end_token} is not indexed.") from e


class Pair(ManagerAccessMixin, BasePair):
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

    def price(self, token: ContractInstance | str, block_id: int | str = "latest") -> Decimal:
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


class Liquidity:
    """Using a method (either managed or live query), fetch liquidity info for v2.Pair"""

    def __init__(self, pair: Pair):
        self._pair = pair

    def get_reserves(self, block_id: int | str = "latest") -> tuple[int, int, int]:
        if isinstance(block_id, int) and block_id < 0:
            block_id += self._pair.chain_manager.blocks.head.number
        return self._pair.contract.getReserves(block_id=block_id)

    def __getitem__(self, token: TokenInstance | str) -> Decimal:
        if self._pair.is_token0(token):
            return Decimal(self.get_reserves()[0]) / Decimal(10 ** self._pair.token0.decimals())

        elif self._pair.is_token1(token):
            return Decimal(self.get_reserves()[1]) / Decimal(10 ** self._pair.token1.decimals())

        else:
            raise ValueError(f"Token {token} not in pair")


class _ManagedLiquidity(Liquidity):
    """Dynamically fetch liquidity information for v2.Pair via RPC"""

    def __init__(self, pair: Pair):
        self._pair = pair
        self.reserve0, self.reserve1, self.last_updated = pair.contract.getReserves()

    def get_reserves(self, block_id: int | str = "latest") -> tuple[int, int, int]:
        if block_id != "latest":
            block_id += self._pair.chain_manager.blocks.head.number
            return super().get_reserves(block_id=block_id)

        # else: return managed cache value
        return self.reserve0, self.reserve1, self.last_updated
