from functools import cached_property
from typing import TYPE_CHECKING

from ape.logging import logger
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from eip712 import EIP712Message, EIP712Type

from .packages import PERMIT2, get_contract_instance
from .universal_router import PERMIT2_PERMIT, PERMIT2_PERMIT_BATCH

if TYPE_CHECKING:
    from ape.api import AccountAPI, BaseAddress


class PermitDetails(EIP712Type):
    token: "address"  # type: ignore[name-defined]  # noqa
    amount: "uint160"  # type: ignore[name-defined]  # noqa
    expiration: "uint48"  # type: ignore[name-defined]  # noqa
    nonce: "uint48"  # type: ignore[name-defined]  # noqa


class PermitSingle(EIP712Message):
    details: PermitDetails
    spender: "address"  # type: ignore[name-defined]  # noqa
    sigDeadline: "uint256"  # type: ignore[name-defined]  # noqa


class PermitBatch(EIP712Message):
    details: list[PermitDetails]
    spender: "address"  # type: ignore[name-defined]  # noqa
    sigDeadline: "uint256"  # type: ignore[name-defined]  # noqa


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
    def DOMAIN(self) -> dict:
        return dict(
            _name_="Permit2",
            _chainId_=self.chain_manager.chain_id,
            _verifyingContract_=self.contract.address,
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
                **self.DOMAIN,
            )
        )

        return PERMIT2_PERMIT(
            args=[
                (
                    permit.__tuple__,  # type: ignore[attr-defined]
                    spender,
                    sigDeadline,
                ),
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
                **self.DOMAIN,
            )
        )

        return PERMIT2_PERMIT_BATCH(
            args=[
                (
                    [p.__tuple__ for p in permits],  # type: ignore[attr-defined]
                    spender,
                    sigDeadline,
                ),
                signature.encode_rsv(),
            ]
        )
