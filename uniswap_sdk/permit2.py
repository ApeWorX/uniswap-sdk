from functools import cached_property
from typing import TYPE_CHECKING

from ape.logging import logger
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from eip712 import EIP712Domain, EIP712Message
from eth_pydantic_types import abi
from pydantic import BaseModel

from .packages import PERMIT2, get_contract_instance
from .universal_router import PERMIT2_PERMIT, PERMIT2_PERMIT_BATCH

if TYPE_CHECKING:
    from ape.api import AccountAPI, BaseAddress


class PermitDetails(BaseModel):
    token: abi.address
    amount: abi.uint160  # type: ignore[name-defined]  # noqa
    expiration: abi.uint48
    nonce: abi.uint48


class PermitSingle(EIP712Message):
    details: PermitDetails
    spender: abi.address
    sigDeadline: abi.uint256


class PermitBatch(EIP712Message):
    details: list[PermitDetails]
    spender: abi.address
    sigDeadline: abi.uint256


class Permit2(ManagerAccessMixin):
    @cached_property
    def contract(self):
        return get_contract_instance(PERMIT2.Permit2, self.chain_manager.chain_id)

    def get_allowance(
        self,
        user: "BaseAddress | AddressType | str",
        token: "BaseAddress | AddressType | str",
        spender: "BaseAddress | AddressType | str",
    ) -> int:
        return self.contract.allowance(user, token, spender).amount

    def get_nonce(
        self,
        user: "BaseAddress | AddressType | str",
        token: "BaseAddress | AddressType | str",
        spender: "BaseAddress | AddressType | str",
    ) -> int:
        return self.contract.allowance(user, token, spender).nonce

    def get_expiration(
        self,
        user: "BaseAddress | AddressType | str",
        token: "BaseAddress | AddressType | str",
        spender: "BaseAddress | AddressType | str",
    ) -> int:
        return self.contract.allowance(user, token, spender).expiration

    @property
    def DOMAIN(self) -> EIP712Domain:
        return EIP712Domain(
            name="Permit2",
            chainId=self.chain_manager.chain_id,
            verifyingContract=self.contract.address,
        )

    def sign_permit(
        self,
        spender: "BaseAddress | AddressType | str",
        permit: PermitDetails,
        signer: "AccountAPI",
        sigDeadline: int | None = None,
    ) -> PERMIT2_PERMIT:
        spender = self.conversion_manager.convert(spender, AddressType)

        if not sigDeadline:
            sigDeadline = permit.expiration

        logger.info(f"Signing permit for spender '{spender}' w/ Permit2 '{self.contract.address}'")
        signature = signer.sign_message(
            PermitSingle(  # type: ignore[call-arg]
                details=permit,
                spender=spender,
                sigDeadline=sigDeadline,
                eip712_domain=self.DOMAIN,  # type: ignore
            )
        )

        return PERMIT2_PERMIT(
            args=[
                (tuple(permit.model_dump().values()), spender, sigDeadline),
                signature.encode_rsv(),
            ]
        )

    def sign_permit_batch(
        self,
        spender: "BaseAddress | AddressType | str",
        permits: list[PermitDetails],
        signer: "AccountAPI",
        sigDeadline: int | None = None,
    ) -> PERMIT2_PERMIT_BATCH:
        spender = self.conversion_manager.convert(spender, AddressType)

        if not sigDeadline:
            sigDeadline = max(p.expiration for p in permits)

        logger.info(
            f"Signing batch permit for spender '{spender}' w/ Permit2 '{self.contract.address}'"
        )
        signature = signer.sign_message(
            PermitBatch(  # type: ignore[call-arg]
                details=permits,
                spender=spender,
                sigDeadline=sigDeadline,
                eip712_domain=self.DOMAIN,  # type: ignore
            )
        )

        return PERMIT2_PERMIT_BATCH(
            args=[
                (
                    [tuple(p.model_dump().values()) for p in permits],
                    spender,
                    sigDeadline,
                ),
                signature.encode_rsv(),
            ]
        )
