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
        df = self.contract.PairCreated.query("*", start_block=-1000)
        pairs = df[df["event_arguments"].apply(
            lambda x: x.get("token0") == token or x.get("token1") == token
        )]["event_arguments"].apply(lambda x: x.get("pair")).to_list()
        yield from map(Pool, pairs)

    def get_all_pools(self) -> Iterator["Pool"]:
        df = self.contract.PairCreated.query("*", start_block=-1000)
        pairs = df["event_arguments"].apply(lambda x: x.get("pair"))
        for pair in pairs:
            yield Pool(pair)


class Pool(ManagerAccessMixin):
    def __init__(self, address: AddressType):
        self.address = address

    @property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)
