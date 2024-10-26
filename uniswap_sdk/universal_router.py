from itertools import cycle
from typing import Any, Callable, ClassVar, Iterable, Optional, Type, Union

from ape.api import ReceiptAPI, TransactionAPI
from ape.contracts import ContractInstance
from ape.exceptions import DecodingError
from ape.managers.base import ManagerAccessMixin
from ape.utils import StructParser, cached_property
from ape_ethereum.ecosystem import parse_type
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_abi.exceptions import InsufficientDataBytes
from eth_abi.packed import encode_packed
from ethpm_types.abi import ABIType, MethodABI
from hexbytes import HexBytes
from pydantic import BaseModel, field_validator

from .packages import UNI_ROUTER, get_contract_instance


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
    definition: ClassVar[list[ABIType]]
    is_revertible: ClassVar[bool] = False

    # NOTE: For parsing live data
    args: list[Any]
    allow_revert: bool = False

    @field_validator("args")
    @classmethod
    def validate_args(cls, args: list) -> list:
        if len(args) != len(cls.definition):
            raise ValueError(
                f"Number of args ({len(args)}) does not match definition ({len(cls.definition)})."
            )

        return args

    def __repr__(self) -> str:
        args_str = ", ".join(f"{def_.name}={arg}" for def_, arg in zip(self.definition, self.args))
        return f"{self.__class__.__name__}({args_str})"

    def __getattr__(self, attr: str) -> Any:
        for idx, abi_type in enumerate(self.definition):
            if abi_type.name == attr:
                return self.args[idx]

        raise AttributeError

    @property
    def command_byte(self) -> int:
        return (Constants._ALLOW_REVERT_FLAG if self.allow_revert else 0x0) | self.type

    def encode_args(self) -> HexBytes:
        parser = StructParser(
            MethodABI(name=self.__class__.__name__, inputs=self.__class__.definition)
        )
        arguments = parser.encode_input(self.args)
        arg_types = [i.canonical_type for i in self.definition]
        python_types = tuple(
            self.provider.network.ecosystem._python_type_for_abi_type(i) for i in self.definition
        )
        converted_args = self.conversion_manager.convert(arguments, python_types)
        encoded_calldata = abi_encode(arg_types, converted_args)
        return HexBytes(encoded_calldata)

    @classmethod
    def _decode_args(cls, calldata: HexBytes) -> list[Any]:
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

        return command_cls(args=command_cls._decode_args(calldata), allow_revert=allow_revert)


def encode_path(path: list) -> bytes:
    if len(path) % 2 != 1:
        ValueError("Path must be an odd-length sequence of token, fee rate, token, ...")

    types = [type for _, type in zip(path, cycle(["address", "uint24"]))]
    return encode_packed(types, path)


def decode_path(path: bytes) -> list:
    decoded_path: list[Union[str, int]] = []
    decoded_type = cycle(["address", "uint24"])
    while len(path) > 0:
        t = next(decoded_type)
        idx = 20 if t == "address" else 3
        data, path = path[:idx], path[idx:]
        decoded_path.extend(abi_decode([t], b"\x00" * (12 if t == "address" else 29) + data))

    return decoded_path


class _V3_EncodePathInput(Command):
    @field_validator("args", mode="before")
    @classmethod
    def encode_path_input(cls, args: list) -> list:
        if isinstance(args[3], list):
            args[3] = encode_path(args[3])

        return args

    def __repr__(self) -> str:
        names = list(def_.name for def_ in self.definition)
        args = self.args.copy()
        names[3] = "path"
        args[3] = self.path
        args_str = ", ".join(f"{name}={arg}" for name, arg in zip(names, args))
        return f"{self.__class__.__name__}({args_str})"

    @property
    def path(self) -> list:
        return decode_path(self.encodedPath)


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
    """Wrap `amountMin` (or more) ether and send to `recipient`"""

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

    @field_validator("args", mode="before")
    @classmethod
    def encode_sub_plan(cls, sub_plan: Union[list, list[Command], "Plan"]) -> list:
        if (
            isinstance(sub_plan, list)
            and len(sub_plan) > 0
            and all(isinstance(e, Plan) for e in sub_plan)
        ):
            sub_plan = Plan(commands=sub_plan)

        # NOTE: Intentionally execute this if above is true
        if isinstance(sub_plan, Plan):
            return [sub_plan.encoded_commands, sub_plan.encode_args()]

        return sub_plan  # should be raw sub plan otherwise (validator check)

    @property
    def decoded_sub_plan(self) -> "Plan":
        return Plan.decode(self.args[0], self.args[1])

    def add_step(self, command: Command):
        self.args[0] += command.command_byte
        self.args[1].append(command.encode_args())

    def rm_step(self):
        if len(self.args[0]) == 0:
            raise ValueError("No more items to pop.")

        self.args[0] = self.args[0][:-1]
        self.args[1].poplast()


class APPROVE_ERC20(Command):
    type = 0x22

    definition = [
        ABIType(name="token", type="address"),
        ABIType(name="spender", type="address"),
    ]


# NOTE: Must come after all the subclassing action above
ALL_COMMANDS_BY_TYPE: dict[int, Type[Command]] = {
    cls.type: cls for cls in Command.__subclasses__() if not cls.__name__.startswith("_")
}
ALL_COMMANDS_BY_NAME: dict[str, Type[Command]] = {
    cls.__name__: cls for cls in Command.__subclasses__() if not cls.__name__.startswith("_")
}


class Plan(BaseModel):
    """
    A plan to execute with the UniversalRouter.
    All of the available plan steps are available as attributes on this class.
    You can also directly add a plan step through `plan.add(...)`

    Usage exmaple::
        >>> plan = Plan().wrap_eth(...)...  # Use the builder pattern to create a plan
        >>> plan.add(V3_SWAP_EXACT_IN(args=...))  # Directly add a command to the plan
    """

    commands: list[Command] = []

    @classmethod
    def decode(cls, encoded_commands: HexBytes, encoded_args: Iterable[HexBytes]) -> "Plan":
        return cls(
            commands=[
                Command.decode(command_byte, encoded_args)
                for command_byte, encoded_args in zip(encoded_commands, encoded_args)
            ]
        )

    def add(self, command: Command) -> "Plan":
        self.commands.append(command)
        return self

    @property
    def encoded_commands(self) -> HexBytes:
        return HexBytes(bytearray([cmd.command_byte for cmd in self.commands]))

    def encode_args(self) -> list[HexBytes]:
        return [cmd.encode_args() for cmd in self.commands]

    def __getattr__(self, command_name: str) -> Callable[..., "Plan"]:
        if command_name.upper() not in ALL_COMMANDS_BY_NAME:
            raise AttributeError(f"Unsupported command type: '{command_name.upper()}'.")

        command_cls = ALL_COMMANDS_BY_NAME[command_name.upper()]

        def add_command(*args, **kwargs):
            # TODO: Conversion and kwarg stripping
            return self.add(command_cls(args=args, **kwargs))

        # TODO: Make `add_command` repr nicely and show args
        add_command.__doc__ = command_cls.__doc__
        return add_command

    # TODO: Add nice __repr__/__str__

    def __dir__(self) -> list[str]:
        return list(n.lower() for n in ALL_COMMANDS_BY_NAME)


class UniversalRouter(ManagerAccessMixin):
    """
    Class for working with the Uniswap UniversalRouter:
        https://docs.uniswap.org/contracts/universal-router/overview

    It is useful to use the :class:`Plan` object to help you create your plans for the router.

    Usage example::
        >>> ur = UniversalRouter()
        >>> plan = Plan().wrap_eth("1 ether").unwrap_eth("1 ether")
        >>> ur.execute(plan, sender=me, value="1 ether")
    """

    constants = Constants

    @cached_property
    def contract(self) -> ContractInstance:
        return get_contract_instance(UNI_ROUTER.UniversalRouter, self.provider.chain_id)

    def decode_plan_from_calldata(self, calldata: HexBytes) -> Plan:
        _, decoded_calldata = self.contract.execute.decode_input(calldata)
        return Plan.decode(decoded_calldata["commands"], decoded_calldata["inputs"])

    def decode_plan_from_transaction(self, txn: Union[str, TransactionAPI, ReceiptAPI]) -> Plan:
        """
        Decode any plan from a transaction object, receipt object,
        or transaction hash (that has been mined)
        """
        if isinstance(txn, str):
            txn = self.provider.get_receipt(txn)

        assert isinstance(txn, (TransactionAPI, ReceiptAPI))  # for mypy
        if txn.receiver != self.contract.address:
            raise ValueError("Cannot decode plan from transaction to different contract.")

        return self.decode_plan_from_calldata(HexBytes(txn.data))

    def plan_as_transaction(
        self, plan: Plan, deadline: Optional[int] = None, **txn_args
    ) -> TransactionAPI:
        """
        Encode the plan as a transaction for further processing
        """
        args: list[Any] = [plan.encoded_commands, plan.encode_args()]

        if deadline is not None:
            args.append(deadline)

        return self.contract.execute.as_transaction(*args, **txn_args)

    def execute(self, plan: Plan, deadline: Optional[int] = None, **txn_args) -> ReceiptAPI:
        """
        Submit the plan as a transaction and broadcast it
        """
        args: list[Any] = [plan.encoded_commands, plan.encode_args()]

        if deadline is not None:
            args.append(deadline)

        return self.contract.execute(*args, **txn_args)
