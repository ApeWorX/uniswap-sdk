from decimal import Decimal
from functools import cache
from itertools import islice, pairwise
from typing import TYPE_CHECKING, Iterator, cast

from ape.contracts import ContractInstance, ContractLog
from ape.logging import get_logger
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, ManagerAccessMixin, cached_property
from ape_ethereum import multicall
from eth_utils import is_checksum_address, to_int
from networkx import DiGraph, Graph, min_cost_flow, shortest_simple_paths

from .packages import V2, get_contract_instance

try:
    from ape_tokens.managers import ERC20  # type: ignore[import-not-found]
except ImportError:
    ERC20 = None

try:
    from itertools import batched
except ImportError:

    # NOTE: `itertools.batched` added in 3.12
    # TODO: Find backport instead
    def batched(iterable, n, *, strict=False):
        # batched('ABCDEFG', 3) → ABC DEF G
        if n < 1:
            raise ValueError("n must be at least one")
        iterator = iter(iterable)
        while batch := tuple(islice(iterator, n)):
            if strict and len(batch) != n:
                raise ValueError("batched(): incomplete batch")
            yield batch


if TYPE_CHECKING:
    from typing import Self

    from silverback import SilverbackBot


logger = get_logger("uniswap_sdk.v2")


class Factory(ManagerAccessMixin):
    """
    Singleton class to interact with a deployment of the Uniswap V2 protocol's Factory contract.

    Usage example::

        >>> from uniswap_sdk import v2
        >>> factory = v2.Factory()
        >>> for pair in factory.get_all_pairs():
        ...     print(pair)  # WARNING: Will take 6 mins or more to fetch
        >>> len(list(factory))  # Cached, almost instantaneous
        396757
        >>> from ape_tokens import tokens
        >>> yfi = tokens["YFI"]
        >>> for pair in factory.get_pairs_by_token(yfi):
        ...     print(pair)  # WARNING: Will take 12 mins or more to index
        >>> len(factory["YFI"])  # Already indexed, almost instantaneous
        3
        >>> pair = factory.get_pair(yfi, tokens["USDC"])  # Single contract call
        <uniswap_sdk.v2.Pair address=0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d>

    """

    def __init__(self) -> None:
        # Memory cache/indexes
        self._last_cached_block = 0
        # TODO: Remove once query system is better
        self._cached_pairs: list["Pair"] = []
        self._indexed_pairs = Graph()
        self._last_indexed: int = 0

    # non-cached functions
    @cached_property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)

    def install(self, bot: "SilverbackBot"):
        from silverback.types import TaskType

        async def index_existing_pairs(snapshot):
            self._index_all_pairs()

        # NOTE: Modify name to namespace it from user tasks
        index_existing_pairs.__name__ = f"uniswap-sdk:{index_existing_pairs.__name__}"
        bot.broker_task_decorator(TaskType.STARTUP)(index_existing_pairs)

        # NOTE: Indexing liquidity and price in real-time reduces amount of work in search algo
        async def index_new_pair(log):
            pair = Pair(log.pair, log.token0, log.token1)
            # Update internal caches
            self._cached_pairs.append(pair)
            self._indexed_pairs.add_edge(log.token0, log.token1, pair=pair)
            self._last_indexed += 1

        # NOTE: Modify name to namespace it from user tasks
        index_new_pair.__name__ = f"uniswap-sdk:{index_new_pair.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=self.contract.PairCreated)(
            index_new_pair
        )

        # NOTE: This runs on all deployed pairs from the factory
        async def sync_pair_liquidity(log):
            pass
            # TODO: How to snag `pair` by address?
            # pair = log.contract_address
            # pair.reserve0 = log.reserve0
            # pair.reserve1 = log.reserve1

        # NOTE: Modify name to namespace it from user tasks
        sync_pair_liquidity.__name__ = f"uniswap-sdk:{sync_pair_liquidity.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=V2.UniswapV2Pair.Sync)(
            sync_pair_liquidity
        )

    def get_pair(self, tokenA: AddressType, tokenB: AddressType) -> "Pair":
        if (pair_address := self.contract.getPair(tokenA, tokenB)) == ZERO_ADDRESS:
            raise ValueError("No deployed pair")

        return Pair(pair_address)

    def search_for_pairs(self, token: AddressType, *others: AddressType) -> Iterator["Pair"]:
        if len(others) == 0:
            raise ValueError("Must give at least one other token to search for a pair")

        for potential_match in others:
            if (pair_address := self.contract.getPair(token, potential_match)) != ZERO_ADDRESS:
                yield Pair(pair_address)

    def __len__(self) -> int:
        return self.contract.allPairsLength()

    # cached functions
    def get_all_pairs(self) -> Iterator["Pair"]:
        yield from iter(self._cached_pairs)

        if len(self) == len(self._cached_pairs):
            return  # Cache is up to date

        if (start_block := self._last_cached_block or self.contract.creation_metadata.block) >= (
            latest_block := self.chain_manager.blocks.head.number
        ):
            return

        for pair in map(
            Pair.from_log,
            self.contract.PairCreated.range(start_block, latest_block),
        ):
            self._cached_pairs.append(pair)
            yield pair

        self._last_cached_block = latest_block
        return  # TODO: Delete below

        # TODO: Reformat to query system when better (using PairCreated)
        if (num_pairs := len(self)) > (last_pair := len(self._cached_pairs)):
            logger.info(f"Caching {num_pairs - last_pair} pairs...")
            # NOTE: This can be faster than brute force way
            while last_pair < num_pairs:
                call = multicall.Call()
                [
                    call.add(self.contract.allPairs, i)
                    for i in range(
                        last_pair,
                        # TODO: Parametrize multicall increment (per network?)
                        min(last_pair + 4_000, num_pairs),  # NOTE: `range` ignores last value
                    )
                ]

                new_pairs = list(map(Pair, call()))
                yield from iter(new_pairs)
                self._cached_pairs.extend(new_pairs)
                last_pair += len(call.calls)

    def __iter__(self) -> Iterator["Pair"]:
        return self.get_all_pairs()

    def _index_all_pairs(self):
        # TODO: Merge logic from `get_all_pairs` and replace `get_all_pairs` w/
        #       `yield from nx.get_edge_attributes(self._indexed_pairs, "pair").values()`
        for pair in islice(self, self._last_indexed, None):
            assert pair._token0_address
            assert pair._token1_address
            self._indexed_pairs.add_edge(
                pair._token0_address,
                pair._token1_address,
                pair=pair,
            )
        # TODO: Remove `_last_indexed` and use `len(self._indexed_pairs.edges)`
        self._last_indexed = len(self)  # Skip indexing loop next time from this height
        return  # TODO: Delete?

        TOKEN0_VIEW_METHOD = V2.UniswapV2Pair.contract_type.view_methods["token0"]
        TOKEN1_VIEW_METHOD = V2.UniswapV2Pair.contract_type.view_methods["token1"]
        # TODO: Once query system is used to pull all pairs, we can remove multi-
        #       call since .token0/.token1 addresses will be known in advance
        call = multicall.Call()
        matching_pairs = list()

        if (unindexed_pairs := len(self) - self._last_indexed) > 0:
            logger.info(f"Indexing {unindexed_pairs} pairs...")

        for pair in islice(self, self._last_indexed, None):
            if pair._token0_address and pair._token1_address:
                self._indexed_pairs.add_edge(
                    pair._token0_address,
                    pair._token1_address,
                    pair=pair,
                )
                continue  # NOTE: Already cached, no need to multicall

            # Add to indexing multicall for later
            # HACK: Just use raw dict instead of `call.add` to avoid `.contract` overhead
            call.calls.append(
                dict(
                    target=pair.address,
                    value=0,
                    allowFailure=True,
                    callData=bytes.fromhex("0dfe1681"),
                )
            )
            call.abis.append(TOKEN0_VIEW_METHOD)
            call.calls.append(
                dict(
                    target=pair.address,
                    value=0,
                    allowFailure=True,
                    callData=bytes.fromhex("d21220a7"),
                )
            )
            call.abis.append(TOKEN1_VIEW_METHOD)
            matching_pairs.append(pair)

            # TODO: Parametrize multicall increment (per network?)
            if len(call.calls) >= 10_000:
                # NOTE: Cache to avoid additional call next time
                for (token0_address, token1_address), pair in zip(
                    batched(call(), 2),
                    matching_pairs,
                ):
                    # Update pair cache for token0/token1
                    # TODO: Remove this step once query support is added to fetch
                    pair._token0_address = token0_address
                    pair._token1_address = token1_address
                    # Add pair to cache
                    # TODO: use `pair.token0/1` once query support is added (Ape v0.9?),
                    #       and `ContractInstance` has `__hash__` (ApeWorX/ape#2476)
                    self._indexed_pairs.add_edge(token0_address, token1_address, pair=pair)

                # NOTE: Reset multicall and matching pairs for next batch
                call = multicall.Call()
                matching_pairs = []

        # Execute remaining unindexed batch (batch size smaller than increment)
        # NOTE: If empty, shouldn't do anything
        for (token0_address, token1_address), pair in zip(
            batched(call(), 2),
            matching_pairs,
        ):
            # Update pair cache for token0/token1
            # TODO: Remove this step once query support is added to fetch
            pair._token0_address = token0_address
            pair._token1_address = token1_address
            # Add pair to cache
            self._indexed_pairs.add_edge(token0_address, token1_address, pair=pair)

        self._last_indexed = len(self)  # Skip indexing loop next time from this height

    def get_pairs_by_token(self, token: AddressType) -> Iterator["Pair"]:
        # Bring index up to date
        self._index_all_pairs()

        # Yield all from index
        for data in self._indexed_pairs[token.address].values():
            yield cast(Pair, data["pair"])

    def __getitem__(self, token: AddressType) -> list["Pair"]:
        return list(self.get_pairs_by_token(token))

    def find_routes(
        self,
        tokenA: AddressType,
        tokenB: AddressType,
        depth: int = 3,
    ) -> Iterator[list[AddressType]]:
        """
        Find all valid routes (sequence of tokens) that takes you from ``tokenA`` to ``tokenB``.
        NOTE: depth >4 takes a long time, unless the number of pairs is small, 3 is recommended
        NOTE: Will return shallowest routes first, as it performs exhaustive BFS
        """
        if depth < 2:
            raise ValueError("Routes less than length 2 do not make sense")

        # Bring index up to date
        self._index_all_pairs()

        for route in shortest_simple_paths(self._indexed_pairs, tokenA, tokenB):
            # NOTE: `shortest_simple_paths` yields shortest to longest, so bail when exceeding depth
            if len(route) > depth:
                break

            yield route

    def all_route_price_liquidity(
        self,
        tokenA: AddressType,
        tokenB: AddressType,
        depth: int = 3,
        block_id: int | str = "latest",
        min_liquidity: Decimal = Decimal(0),
    ) -> tuple[list[Decimal], list[Decimal]]:
        route_price: list[Decimal] = []
        route_liquidity: list[Decimal] = []
        for route in self.find_routes(tokenA, tokenB, depth=depth):
            price = Decimal(1)  # 1:1 price (muliplied by ratio at each edge)
            liquidity = Decimal(256**1)  # NOTE: Maximum possible token amount (used w/ `min`)
            for token0, token1 in pairwise(route):
                pair = self._indexed_pairs.get_edge_data(token0, token1)["pair"]
                if pair.is_token0(token0):
                    reserve0, reserve1, _ = pair.get_reserves(block_id=block_id)
                else:
                    reserve1, reserve0, _ = pair.get_reserves(block_id=block_id)

                # Measure liquidity in terms of tokenA
                if (liquidity := min(liquidity, reserve0 / price)) < min_liquidity or reserve1 == 0:
                    break  # NOTE: not liquid enough to compute further (also skip)

                # NOTE: Pair is liquid enough (avoids Div/0 fault computing price)
                # Price of base in terms of target
                price *= reserve1 / reserve0

            else:
                # Append aggregates for route
                # NOTE: Only do this if route meets liquidity requirements
                #       (`else` branch in `for` only happens if no `break` occurs)
                route_price.append(price)
                route_liquidity.append(liquidity)

        if not route_price:
            raise ValueError("No routes found that meet liquidity requirements.")

        assert len(route_price) == len(route_liquidity), "Invariant failure"

        return route_price, route_liquidity

    def average_market_price(
        self,
        tokenA: AddressType,
        tokenB: AddressType,
        depth: int = 3,
        block_id: int | str = "latest",
        min_liquidity: Decimal = Decimal(0),
    ) -> Decimal:
        route_price, route_liquidity = self.all_route_price_liquidity(
            tokenA,
            tokenB,
            depth=depth,
            block_id=block_id,
            min_liquidity=min_liquidity,
        )

        return Decimal(
            sum(liquidity * price for liquidity, price in zip(route_liquidity, route_price))
        ) / Decimal(sum(route_liquidity))

    def solve_best_path_flow(
        self,
        tokenA: AddressType,
        tokenB: AddressType,
        depth: int = 3,
        amount_in: Decimal | None = None,
        amount_out: Decimal | None = None,
        min_liquidity: Decimal = Decimal(0),
        max_slippage: Decimal = Decimal("0.005"),  # TODO: move to settings?
    ) -> Iterator[tuple[list[AddressType], Decimal]]:
        if not (bool(amount_in is None) ^ bool(amount_out is None)):
            raise ValueError("Must specify exactly one of `amount_in=` or `amount_out=` to solve.")

        elif amount_in is None:
            assert isinstance(amount_out, Decimal)  # mypy happy
            amount_in = amount_out / self.average_market_price(
                tokenA, tokenB, min_liquidity=min_liquidity
            )

        assert isinstance(amount_in, Decimal)  # mypy happy

        # Create well-connected directed graph from subgraph of routes between A and B
        # NOTE: We are going to solve the "min cost flow problem" with this directed graph
        connected_pairs = DiGraph()
        for route in self.find_routes(tokenA, tokenB, depth=depth):
            price = Decimal(1)

            for token0, token1 in pairwise(route):
                pair = self._indexed_pairs.get_edge_data(token0, token1)["pair"]
                if pair.is_token0(token0):
                    reserve0, reserve1, _ = pair.get_reserves()
                else:
                    reserve1, reserve0, _ = pair.get_reserves()

                if (liquidity := reserve0 / price) < min_liquidity or reserve1 == 0:
                    # NOTE: not liquid enough to compute this route further
                    #       (will leave this unconnected edge hanging in graph)
                    break

                # NOTE: Compute `capacity` as swap amount that maintains slippage <= limit
                #
                #       Dx = y_0 / (Pm * (1 - S)) - x_0
                #       Dy = y_0 * (1 - x_0 / (Dx + x_0))
                #
                #       where,
                #           x_0 := starting liquidity of entry token
                #           y_0 := starting liquidity of exit token
                #           x_1 := ending liquidity of entry token
                #           y_1 := ending liquidity of exit token
                #           Dx := difference in liquidity of entry token (x_1 - x_0)
                #           Dy := difference in liquidity of exit token (y_0 - y_1)
                #           Pm := "market" reference price
                #           S := desired slippage parameter
                #
                #       For the min cost flow algorithm, the `capacity` is the amount that gets
                #       "used" by traversing this edge, so we want to limit swapping through
                #       the pair so that the total slippage remains below the limit by applying
                #       the limit to every possible path in a way the algorithm will respect.

                # capacity = reserve1 / (price * (1 - max_slippage)) - reserve0
                # assert capacity < liquidity  # due to x*y=K curve, this should always hold

                # NOTE: Compute `weight` as the difference between output of a swap of `capacity`
                #       amount using the ideal swap price vs. actual swap price, and include a
                #       heuristic for the fees and gas cost of doing the swap. Note that the ideal
                #       price is simply based on the price of the entire route in terms of `tokenA`.
                #
                #       For the min cost flow algorithm, the `weight` is the unit cost accrued by
                #       selecting an edge to traverse, so we want to create a heuristic that
                #       represents "total loss added" so that the algorithm will find the best path
                #       that minimizes what we can think of as "conversion loss". This heuristic
                #       must be a constant linear factor, so instead of computing slippage directly
                #       we are expressing the full loss if all of capacity is used.

                weight = reserve1 * reserve0 / (amount_in + reserve0)
                assert weight > 0

                connected_pairs.add_edge(
                    token0,
                    token1,
                    # NOTE: Adjust to large integer value
                    weight=int(weight * 10**18),
                    # NOTE: Adjust to large integer value
                    capacity=int(liquidity * 10**18),
                )
                price *= reserve1 / reserve0

        # Lastly, add the demand to graph start node and end node
        # (converting value in terms of `tokenA` adjusted to large integer value)
        connected_pairs.add_node(tokenA, demand=-amount_in * 10**18)
        connected_pairs.add_node(tokenB, demand=amount_in * 10**18)

        # Solve the min-cost, max-flow problem
        solution = min_cost_flow(connected_pairs)

        # NOTE: Prune solution of paths w/ 0% flows
        def prune_routes(routes: dict) -> dict:
            # Remove zeros and make adjust integer values back to where they were
            return {
                k: prune_routes(v) if isinstance(v, dict) else Decimal(v) / 10**18
                for k, v in routes.items()
                if v and (not isinstance(v, dict) or prune_routes(v))
            }

        def flatten_routes(
            start: AddressType,
            end: AddressType,
            routes: dict[AddressType, dict[AddressType, Decimal]],
        ) -> Iterator[tuple[list[AddressType], Decimal]]:
            if start == end:  # End recursion
                yield [end], Decimal(-1)  # NOTE: We don't use this
                return

            for target, weight in routes[start].items():
                for inner_path, _ in flatten_routes(target, end, routes):
                    yield [start, *inner_path], weight

        yield from flatten_routes(tokenA, tokenB, prune_routes(solution))


class Pair(ManagerAccessMixin):
    """
    Represents a UniswapV2Pair contract, which implements swaps between two tokens
    according to the x*y=k constant product market maker function

    Usage example::

        >>> from uniswap_sdk import v2
        >>> pair = v2.Pair(address="0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d")
        >>> pair["YFI"]  # Get reserves of token in pair (in appropiate decimals)
        Decimal('0.000010265...')
        >>> print(f"Price is {pair.price('YFI'):0,.2f} [YFI/{pair.other('YFI').symbol()}]")
        Price is 2,196.81 [YFI/USDC]

    """

    def __init__(
        self,
        address: AddressType,
        token0: ContractInstance | AddressType | None = None,
        token1: ContractInstance | AddressType | None = None,
    ):
        self.address = address
        # Cache if available
        if isinstance(token0, ContractInstance):
            self.token0 = token0
            self._token0_address = token0.address
        elif token0:
            self._token0_address = token0

        if isinstance(token1, ContractInstance):
            self.token1 = token1
            self._token1_address = token1.address
        elif token1:
            self._token1_address = token1

    @classmethod
    def from_log(cls, log: ContractLog) -> "Self":
        return cls(
            address=log.pair,
            token0=log.token0,
            token1=log.token1,
        )

    def __hash__(self) -> int:
        return to_int(hexstr=self.address)

    def __eq__(self, other) -> bool:
        return isinstance(other, Pair) and self.address == other.address

    def __repr__(self) -> str:
        return f"<{self.__class__.__module__}.{self.__class__.__name__} address={self.address}>"

    @cached_property
    def contract(self) -> ContractInstance:
        return self.chain_manager.contracts.instance_at(
            self.address, contract_type=V2.UniswapV2Pair.contract_type
        )

    @cached_property
    def token0(self) -> ContractInstance:
        return self.chain_manager.contracts.instance_at(
            self._token0_address or self.contract.token0(), contract_type=ERC20
        )

    @cached_property
    def token0_symbol(self) -> str:
        return self.token0.symbol()

    @cached_property
    def token0_decimals(self) -> int:
        return self.token0.decimals()

    @cached_property
    def token1(self) -> ContractInstance:
        return self.chain_manager.contracts.instance_at(
            self._token1_address or self.contract.token1(), contract_type=ERC20
        )

    @cached_property
    def token1_symbol(self) -> str:
        return self.token1.symbol()

    @cached_property
    def token1_decimals(self) -> int:
        return self.token1.decimals()

    @cache
    def is_token0(self, token: str) -> bool:
        if is_checksum_address(token):
            return self.token0.address == token
        else:
            return self.token0_symbol == token

    @cache
    def is_token1(self, token: str) -> bool:
        if is_checksum_address(token):
            return self.token1.address == token
        else:
            return self.token1_symbol == token

    def get_reserves(self, block_id: int | str = "latest") -> tuple[Decimal, Decimal, int]:
        if isinstance(block_id, int) and block_id < 0:
            block_id += self.chain_manager.blocks.head.number

        raw_reserve0, raw_reserve1, last_block = self.contract.getReserves(block_id=block_id)

        return (
            Decimal(raw_reserve0) / Decimal(10**self.token0_decimals),
            Decimal(raw_reserve1) / Decimal(10**self.token1_decimals),
            last_block,
        )

    def __getitem__(self, token: ContractInstance | str) -> Decimal:
        if isinstance(token, ContractInstance):
            token = token.address

        if self.is_token0(token):
            return self.get_reserves()[0]
        elif self.is_token1(token):
            return self.get_reserves()[1]
        else:
            raise ValueError(f"Token {token} not in pair")

    def other(self, token: ContractInstance | str) -> ContractInstance:
        """
        Get the other token in the pair that isn't ``token``.
        """
        if isinstance(token, ContractInstance):
            token = token.address

        if self.is_token0(token):
            return self.token1

        elif self.is_token1(token):
            return self.token0

        raise ValueError(f"Token {token} is not one of the tokens in the pair")

    def price(self, token: ContractInstance | str, block_id: int | str = "latest") -> Decimal:
        """
        Price of ``token`` relative to the other token in the pair.
        """
        token0_reserve, token1_reserve, _ = self.get_reserves(block_id=block_id)

        if self.is_token0(token.address if isinstance(token, ContractInstance) else token):
            return token1_reserve / token0_reserve
        else:
            return token0_reserve / token1_reserve
