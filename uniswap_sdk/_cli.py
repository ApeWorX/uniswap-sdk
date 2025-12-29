from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated

import click
from ape.cli import ConnectedProviderCommand, account_option, network_option, verbosity_option
from ape.types import AddressType
from ape_tokens import Token, tokens
from pydantic import Field

from uniswap_sdk import Uniswap


def token_arg(arg_name: str, **kwargs):
    return click.argument(arg_name, metavar=arg_name.upper(), **kwargs)


def intermediate_tokens():
    return click.option(
        "-T",
        "--token",
        "filter_tokens",
        metavar="TOKEN",
        multiple=True,
        default=[],
        help="Tokens to index routes for.",
    )


@click.group()
def cli():
    """Commands for working with the Uniswap Protocol"""


@cli.command(cls=ConnectedProviderCommand)
@network_option()
@token_arg("base")
@token_arg("quote")
@intermediate_tokens()
@click.option(
    "-L",
    "--min-liquidity",
    type=Decimal,
    default=Decimal(1),
    help="Minimum amount of liquidity (in BASE) to filter routes by when determining price.",
)
def price(base, quote, filter_tokens, min_liquidity):
    """Get price of BASE in terms of QUOTE"""
    from ape import convert
    from ape.types import AddressType

    base = Token.at(convert(base, AddressType))
    quote = Token.at(convert(quote, AddressType))

    uni = Uniswap()
    list(uni.index(tokens=[base, *(filter_tokens or tokens), quote]))

    price = uni.price(
        base=base,
        quote=quote,
        min_liquidity=min_liquidity,
    )

    click.echo(f"{price:.04f} [{base.symbol()}/{quote.symbol()}]")


@cli.command(cls=ConnectedProviderCommand)
@network_option()
@account_option()
@token_arg("have")
@token_arg("want")
@intermediate_tokens()
@click.option(
    "-I",
    "--amount-in",
    default=None,
    help="Required amount to send for swap.",
)
@click.option(
    "--max-amount-in",
    default=None,
    help="Required maximum amount to send for swap.",
)
@click.option(
    "-O",
    "--amount-out",
    default=None,
    help="Required amount to receive from swap.",
)
@click.option(
    "--min-amount-out",
    default=None,
    help="Required minimum amount to receive from swap.",
)
@click.option(
    "-S",
    "--slippage",
    default=None,
    help="Slippage to use for swap. Defaults to package configuration.",
)
@click.option(
    "-R",
    "--receiver",
    default=None,
    help="Send tokens to this address. Defaults to selected account.",
)
def swap(
    have,
    want,
    filter_tokens,
    amount_in,
    max_amount_in,
    amount_out,
    min_amount_out,
    slippage,
    receiver,
    account,
):
    """Swap HAVE for WANT using Uniswap"""
    from ape import convert
    from ape.types import AddressType

    have = Token.at(convert(have, AddressType))
    want = Token.at(convert(want, AddressType))

    uni = Uniswap()
    list(uni.index(tokens=[have, *(filter_tokens or tokens), want]))

    uni.swap(
        have=have,
        want=want,
        amount_in=amount_in,
        amount_out=amount_out,
        slippage=slippage,
        receiver=receiver,
        sender=account,
    )


@cli.command(cls=ConnectedProviderCommand)
@verbosity_option(default=100_000)  # NOTE: Disabled
@network_option()
@account_option()
@intermediate_tokens()
def mcp(ecosystem, network, filter_tokens, account):
    """Start the Uniswap MCP Server"""

    try:
        from fastmcp import Context, FastMCP

    except ImportError:
        raise click.UsageError("Must install the `[mcp]` extra to use this command.")

    @asynccontextmanager
    async def lifespan(server):
        uni = Uniswap()
        list(uni.index(tokens=(filter_tokens or tokens)))
        yield uni

    server = FastMCP(
        name=f"Uniswap Protocol on {ecosystem.name}:{network.name}",
        lifespan=lifespan,
        instructions=f"""
        # Uniswap MCP Server

        This server provides capabilities for pricing and swapping tokens
        using the Uniswap protocol on {ecosystem.name}:{network.name}
        for the user account {account.address}.
        """,
    )

    # TODO: Move this to ape-tokens?
    @server.tool()
    async def get_token_balance(
        token: Annotated[str | AddressType, Field(description="The token symbol or address")],
    ) -> Decimal:
        """Get the balance of `token` in the user's account."""

        if token in ("ether", "ETH"):
            return account.balance * Decimal("1e-18")

        from ape import convert
        from ape.types import AddressType

        token = Token.at(convert(token, AddressType))
        return token.balanceOf(account) * Decimal(  # type: ignore[attr-defined]
            f"1e-{token.decimals()}"  # type: ignore[attr-defined]
        )

    @server.tool()
    async def get_price(
        ctx: Context,
        base: Annotated[
            str | AddressType,
            Field(description="The token symbol or address you want to know the price of"),
        ],
        quote: Annotated[
            str | AddressType,
            Field(description="The token symbol or address which the price will be expressed"),
        ],
    ) -> Decimal:
        """
        Returns the current exchange rate between two tokens, `base` and `quote`, as observed
        across all relevant markets in the Uniswap protocol. This price reflects the starting rate
        at which a trade on Uniswap will begin, and it does not include slippage or market impact
        from conducting an actual trade. due to the mechanics of the Uniswap AMM model.

        **Important Notes**:
        1. It is only intended to use this price as a reference.
        2. The number will be returned as a decimal value, reflecting the precision of the market
           price. Do not scale or re-interpret this number.
        3. The number should be interpretted as being the number of `quote` tokens that equals
           exactly 1 `base` token by the current market, or in the context of `quote` per `base`.
        """

        uni = ctx.request_context.lifespan_context
        return uni.price(base, quote)

    @server.tool()
    async def swap(
        ctx: Context,
        have: Annotated[
            str | AddressType,
            Field(description="The token symbol or address you want to sell"),
        ],
        want: Annotated[
            str | AddressType,
            Field(description="The token symbol or address you want to buy"),
        ],
        amount_in: Annotated[
            Decimal | None,
            Field(
                description="The amount of `have` tokens you want to sell."
                " Leave empty if using `amount_out`."
            ),
        ] = None,
        max_amount_in: Annotated[
            Decimal | None,
            Field(
                description="The maximum amount of `have` tokens you are willing to sell."
                " Leave empty to auto-compute this amount when `amount_out` is provided."
            ),
        ] = None,
        amount_out: Annotated[
            Decimal | None,
            Field(
                description="The amount of `want` tokens you want to buy."
                " Leave empty if using `amount_in`."
            ),
        ] = None,
        min_amount_out: Annotated[
            Decimal | None,
            Field(
                description="The minimum amount of `want` tokens you want to buy."
                " Leave empty to auto-compute this amount when `amount_in` is provided."
            ),
        ] = None,
        slippage: Annotated[
            Decimal | None,
            Field(
                description="""
                The maximum change in equilibrium price you are willing to accept.
                Quantity is a value-less ratio, convert to a ratio if user specifies a percent.
                Leave empty to use the default of `0.005` (0.5%),
                or when `max_amount_in`/`min_amount_out` are provided.
                """
            ),
        ] = None,
    ) -> str:
        """
        Performs a token swap, converting an amount of have tokens into want tokens. This function
        is designed to execute trades on-chain and accounts for real-world dynamics such as
        slippage and market impact.

        **Important Note**: This function will account for market shifts, which can be set by the
        `slippage`, `max_amount_in`, or `min_amount_out` parameters. Use these to protect against
        adverse market changes while executing the user's order.
        """

        # NOTE: FastMCP doesn't actually support `Decimal` auto-casting yet
        if isinstance(amount_in, str):
            amount_in = Decimal(amount_in)
        if isinstance(max_amount_in, str):
            max_amount_in = Decimal(max_amount_in)
        if isinstance(amount_out, str):
            amount_out = Decimal(amount_out)
        if isinstance(min_amount_out, str):
            min_amount_out = Decimal(min_amount_out)

        uni = ctx.request_context.lifespan_context
        receipt = uni.swap(
            have=have,
            want=want,
            amount_in=amount_in,
            max_amount_in=max_amount_in,
            amount_out=amount_out,
            min_amount_out=min_amount_out,
            slippage=slippage,
            sender=account,
            confirmations_required=0,
        )
        return str(receipt.txn_hash)

    server.run()
