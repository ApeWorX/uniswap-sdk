from typing import Iterator

from ape.contracts import ContractInstance
from ape.types import AddressType
from ape.utils import ManagerAccessMixin

from .packages import V2, get_contract_instance


class Factory(ManagerAccessMixin):
    @property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)

    def get_pools(self, token: AddressType) -> Iterator["Pool"]:
        # TODO: Use query manager to search once topic filtering is available
        # TODO: Remove `start_block=-1000` once we fix the query system.
        df = self.contract.PairCreated.query("event_arguments", start_block=-1000)
        df_pairs = df.loc[(df["token0"] == token) | (df["token1"] == token)]
        yield from map(Pool, df_pairs["pair"])

    def get_all_pools(self) -> Iterator["Pool"]:
        # TODO: Remove `start_block=-1000` once we fix the query system.
        df = self.contract.PairCreated.query("event_arguments", start_block=-1000)
        yield from map(Pool, df["pair"])


class Pool(ManagerAccessMixin):
    def __init__(self, address: AddressType):
        self.address = address

    @property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)
