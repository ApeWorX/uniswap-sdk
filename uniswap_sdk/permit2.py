from typing import List

from eip712 import EIP712Message, EIP712Type


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
    details: List[PermitDetails]
    spender: "address"  # type: ignore[name-defined]  # noqa
    sigDeadline: "uint256"  # type: ignore[name-defined]  # noqa
