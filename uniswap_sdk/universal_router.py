from itertools import cycle
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Type

from ape.api import ReceiptAPI, TransactionAPI
from ape.contracts import ContractInstance
from ape.exceptions import DecodingError
from ape.managers import ManagerAccessMixin, ProjectManager
from ape.utils import StructParser, cached_property
from ape_ethereum.ecosystem import parse_type
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import InsufficientDataBytes
from eth_abi.packed import encode_packed
from ethpm_types.abi import ABIType, MethodABI
from hexbytes import HexBytes
from pydantic import BaseModel, field_validator


# NOTE: Special constants
class Constants:
    # internal constants
    _ALLOW_REVERT_FLAG = 0x80
    _COMMAND_TYPE_MASK = 0x3F

    # Used for identifying cases when this contract's balance of a token is to be used as an input
    CONTRACT_BALANCE = int(2**255)
    # Used for identifying cases when a v2 pair has already received input tokens
    ALREADY_PAID = 0
    # Used as a flag for identifying the transfer of ETH instead of a token
    ETH = "0x0000000000000000000000000000000000000000"
    # Used as a flag for identifying that msg.sender should be used
    MSG_SENDER = "0x0000000000000000000000000000000000000001"
    # Used as a flag for identifying address(this) should be used
    ADDRESS_THIS = "0x0000000000000000000000000000000000000002"


class Command(BaseModel, ManagerAccessMixin):
    # NOTE: Define in class defs
    type: ClassVar[int]
    definition: ClassVar[List[ABIType]]
    is_revertible: ClassVar[bool] = False

    # NOTE: For parsing live data
    inputs: List[Any]
    allow_revert: bool = False

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, inputs: List) -> List:
        if len(inputs) != len(cls.definition):
            raise ValueError(
                f"Number of args ({len(inputs)}) does not match definition ({len(cls.definition)})."
            )

        return inputs

    def __repr__(self) -> str:
        inputs_str = ", ".join(
            f"{def_.name}={arg}" for def_, arg in zip(self.definition, self.inputs)
        )
        return f"{self.__class__.__name__}({inputs_str})"

    @property
    def command_byte(self) -> int:
        return (Constants._ALLOW_REVERT_FLAG if self.allow_revert else 0x0) | self.type

    def encode_inputs(self) -> HexBytes:
        parser = StructParser(
            MethodABI(name=self.__class__.__name__, inputs=self.__class__.definition)
        )
        arguments = parser.encode_input(self.inputs)
        input_types = [i.canonical_type for i in self.definition]
        python_types = tuple(
            self.provider.network.ecosystem._python_type_for_abi_type(i) for i in self.definition
        )
        converted_args = self.conversion_manager.convert(arguments, python_types)
        encoded_calldata = abi_encode(input_types, converted_args)
        return HexBytes(encoded_calldata)

    @classmethod
    def _decode_inputs(cls, calldata: HexBytes) -> List[Any]:
        raw_input_types = [i.canonical_type for i in cls.definition]
        input_types = [parse_type(i.model_dump(mode="json")) for i in cls.definition]

        try:
            raw_input_values = abi_decode(raw_input_types, calldata, strict=False)
        except InsufficientDataBytes as err:
            raise DecodingError(str(err)) from err

        return [
            cls.provider.network.ecosystem.decode_primitive_value(v, t)
            for v, t in zip(raw_input_values, input_types)
        ]

    @classmethod
    def decode(cls, command_byte: int, calldata: HexBytes):
        command_type = command_byte & Constants._COMMAND_TYPE_MASK

        if command_type not in ALL_COMMANDS_BY_TYPE:
            raise NotImplementedError(f"Unsupported command type: '{command_type}'")

        command_cls = ALL_COMMANDS_BY_TYPE[command_type]
        allow_revert = bool(command_byte & Constants._ALLOW_REVERT_FLAG)

        if allow_revert and not command_cls.is_revertible:
            raise ValueError("Command is not reversible but reversibility is set.")

        return command_cls(inputs=command_cls._decode_inputs(calldata), allow_revert=allow_revert)


def encode_path(path: List) -> bytes:
    if len(path) % 2 != 1:
        ValueError("Path must be an odd-length sequence of token, fee rate, token, ...")

    types = [type for _, type in zip(path, cycle(["address", "uint24"]))]
    return encode_packed(types, path)


class _V3_EncodePathInput:
    @field_validator("inputs", mode="before")
    @classmethod
    def encode_path_input(cls, inputs: List) -> List:
        if isinstance(inputs[3], list):
            inputs[3] = encode_path(inputs[3])

        return inputs


class V3_SWAP_EXACT_IN(_V3_EncodePathInput, Command):
    type = 0x00

    definition = [
        ABIType(name="recipient", type="address"),
        ABIType(name="amountIn", type="uint256"),
        ABIType(name="amountOutMin", type="uint256"),
        ABIType(name="encodedPath", type="bytes"),
        ABIType(name="payerIsUser", type="bool"),
    ]


class V3_SWAP_EXACT_OUT(_V3_EncodePathInput, Command):
    type = 0x01

    definition = [
        ABIType(name="recipient", type="address"),
        ABIType(name="amountOut", type="uint256"),
        ABIType(name="amountInMax", type="uint256"),
        ABIType(name="encodedPath", type="bytes"),
        ABIType(name="payerIsUser", type="bool"),
    ]


class PERMIT2_TRANSFER_FROM(Command):
    type = 0x02

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="recipient", type="address"),
        ABIType(name="amount", type="uint160"),
    ]


class PERMIT2_PERMIT_BATCH(Command):
    type = 0x03

    definition = [
        ABIType(
            name="details",
            type="tuple[]",
            components=[
                ABIType(name="token", type="address"),
                ABIType(name="amount", type="uint160"),
                ABIType(name="expiration", type="uint48"),
                ABIType(name="nonce", type="uint48"),
            ],
        ),
        ABIType(name="spender", type="address"),
        ABIType(name="deadline", type="uint256"),
    ]


class SWEEP(Command):
    type = 0x04

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="recipient", type="address"),
        ABIType(name="amountMin", type="uint256"),
    ]


class TRANSFER(Command):
    type = 0x05

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="recipient", type="address"),
        ABIType(name="amount", type="uint256"),
    ]


class PAY_PORTION(Command):
    type = 0x06

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="recipient", type="address"),
        ABIType(name="bips", type="uint256"),
    ]


class V2_SWAP_EXACT_IN(Command):
    type = 0x08

    definition = [
        ABIType(name="recipient", type="address"),
        ABIType(name="amountIn", type="uint256"),
        ABIType(name="amountOutMin", type="uint256"),
        ABIType(name="path", type="address[]"),
        ABIType(name="payerIsUser", type="bool"),
    ]


class V2_SWAP_EXACT_OUT(Command):
    type = 0x09

    definition = [
        ABIType(name="recipient", type="address"),
        ABIType(name="amountOut", type="uint256"),
        ABIType(name="amountInMax", type="uint256"),
        ABIType(name="path", type="address[]"),
        ABIType(name="payerIsUser", type="bool"),
    ]


class PERMIT2_PERMIT(Command):
    type = 0x0A

    definition = [
        ABIType(
            name="details",
            type="tuple",
            components=[
                ABIType(name="token", type="address"),
                ABIType(name="amount", type="uint160"),
                ABIType(name="expiration", type="uint48"),
                ABIType(name="nonce", type="uint48"),
            ],
        ),
        ABIType(name="spender", type="address"),
        ABIType(name="deadline", type="uint256"),
    ]


class WRAP_ETH(Command):
    type = 0x0B

    definition = [
        ABIType(name="recipient", type="address"),
        ABIType(name="amountMin", type="uint256"),
    ]


class UNWRAP_WETH(Command):
    type = 0x0C

    definition = [
        ABIType(name="recipient", type="address"),
        ABIType(name="amountMin", type="uint256"),
    ]


class PERMIT2_TRANSFER_FROM_BATCH(Command):
    type = 0x0D

    definition = [
        ABIType(
            name="batch",
            type="tuple[]",
            components=[
                ABIType(name="sender", type="address"),
                ABIType(name="recipient", type="address"),
                ABIType(name="amount", type="uint160"),
                ABIType(name="token", type="address"),
            ],
        )
    ]


class BALANCE_CHECK_ERC20(Command):
    type = 0x0E

    definition = [
        ABIType(name="owner", type="address"),
        ABIType(name="token", type="address"),
        ABIType(name="minBalance", type="uint256"),
    ]


class SEAPORT_V1_5(Command):
    type = 0x10

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class LOOKS_RARE_V2(Command):
    type = 0x11

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class NFTX(Command):
    type = 0x12

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class CRYPTOPUNKS(Command):
    type = 0x13

    definition = [
        ABIType(name="punk_id", type="uint256"),
        ABIType(name="recipient", type="address"),
        ABIType(name="value", type="uint256"),
    ]


class OWNER_CHECK_721(Command):
    type = 0x15

    definition = [
        ABIType(name="owner", type="address"),
        ABIType(name="token", type="address"),
        ABIType(name="token_id", type="uint256"),
    ]


class OWNER_CHECK_1155(Command):
    type = 0x16

    definition = [
        ABIType(name="owner", type="address"),
        ABIType(name="token", type="address"),
        ABIType(name="token_id", type="uint256"),
        ABIType(name="min_balance", type="uint256"),
    ]


class SWEEP_ERC721(Command):
    type = 0x17

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="recipient", type="address"),
        ABIType(name="token_id", type="uint256"),
    ]


class X2Y2_721(Command):
    type = 0x18

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
        ABIType(name="recipient", type="address"),
        ABIType(name="token", type="address"),
        ABIType(name="token_id", type="uint256"),
    ]


class SUDOSWAP(Command):
    type = 0x19

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class NFT20(Command):
    type = 0x1A

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class X2Y2_1155(Command):
    type = 0x1B

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
        ABIType(name="recipient", type="address"),
        ABIType(name="token", type="address"),
        ABIType(name="token_id", type="uint256"),
        ABIType(name="amount", type="uint256"),
    ]


class FOUNDATION(Command):
    type = 0x1C

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
        ABIType(name="recipient", type="address"),
        ABIType(name="token", type="address"),
        ABIType(name="token_id", type="uint256"),
    ]


class SWEEP_ERC1155(Command):
    type = 0x1D

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="recipient", type="address"),
        ABIType(name="token_id", type="uint256"),
        ABIType(name="amount", type="uint256"),
    ]


class ELEMENT_MARKET(Command):
    type = 0x1E

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class SEAPORT_V1_4(Command):
    type = 0x20

    definition = [
        ABIType(name="value", type="uint256"),
        ABIType(name="data", type="bytes"),
    ]


class EXECUTE_SUB_PLAN(Command):
    type = 0x21

    definition = [
        ABIType(name="commands", type="bytes"),
        ABIType(name="inputs", type="bytes[]"),
    ]


class APPROVE_ERC20(Command):
    type = 0x22

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="spender", type="uint8"),  # 0 = opensea condiut, 1 = sudoswap
    ]


# NOTE: Must come after all the subclassing action above
ALL_COMMANDS_BY_TYPE: Dict[int, Type[Command]] = {cls.type: cls for cls in Command.__subclasses__()}
ALL_COMMANDS_BY_NAME: Dict[str, Type[Command]] = {
    cls.__name__: cls for cls in Command.__subclasses__()
}


class Plan(BaseModel):
    commands: List[Command] = []

    @classmethod
    def decode(cls, encoded_commands: HexBytes, encoded_inputs: Iterable[HexBytes]) -> "Plan":
        return cls(
            commands=[
                Command.decode(command_byte, encoded_args)
                for command_byte, encoded_args in zip(encoded_commands, encoded_inputs)
            ]
        )

    def add(self, command: Command) -> "Plan":
        self.commands.append(command)
        return self

    @property
    def encoded_commands(self) -> HexBytes:
        return HexBytes(bytearray([cmd.command_byte for cmd in self.commands]))

    def encode_inputs(self) -> List[HexBytes]:
        return [cmd.encode_inputs() for cmd in self.commands]

    def __getattr__(self, command_name: str) -> Callable[..., "Plan"]:
        if command_name.upper() not in ALL_COMMANDS_BY_NAME:
            raise AttributeError(f"Unsupported command type: '{command_name.upper()}'.")

        command_cls = ALL_COMMANDS_BY_NAME[command_name.upper()]

        def add_command(*args, **kwargs):
            return self.add(command_cls(inputs=args, **kwargs))

        return add_command


class UniversalRouter(ManagerAccessMixin):
    @classmethod
    def load_project(cls) -> ProjectManager:
        raise NotImplementedError

    @cached_property
    def project(self) -> ProjectManager:
        return self.__class__.load_project()

    @cached_property
    def contract(self) -> ContractInstance:
        raise NotImplementedError

    @classmethod
    def inject(cls, *deploy_args, **tx_args) -> "UniversalRouter":
        self = cls()
        # NOTE: Override the cached property value since we are creating it manually
        self.contract = self.project.UniversalRouter.deploy(*deploy_args, **tx_args)
        return self

    def decode_plan_from_calldata(self, calldata: HexBytes) -> Plan:
        encoded_commands, encoded_inputs, _ = self.contract.execute.decode_args(calldata)
        return Plan.decode(encoded_commands, encoded_inputs)

    def decode_plan_from_transaction(self, txn: TransactionAPI) -> Plan:
        if txn.receiver != self.contract.address:
            raise ValueError("Cannot decode plan from transaction to different contract.")

        return self.decode_plan_from_calldata(HexBytes(txn.data))

    def create_transaction_from_plan(
        self, plan: Plan, deadline: Optional[int] = None, **txn_args
    ) -> TransactionAPI:
        args: List[Any] = [plan.encoded_commands, plan.encode_inputs()]

        if deadline is not None:
            args.append(deadline)

        return self.contract.execute.as_transaction(*args, **txn_args)

    def execute_plan(self, plan: Plan, deadline: Optional[int] = None, **txn_args) -> ReceiptAPI:
        args: List[Any] = [plan.encoded_commands, plan.encode_inputs()]

        if deadline is not None:
            args.append(deadline)

        return self.contract.execute(*args, **txn_args)
