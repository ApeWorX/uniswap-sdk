from contextlib import asynccontextmanager
from decimal import Decimal

import click
from ape.cli import ConnectedProviderCommand, account_option, network_option
from ape_tokens import Token, tokens

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
        help="Tokens to index intermediate routes for.",
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
        using the Uniswap protocol on {ecosystem.name}:{network.name}.
        """,
    )

    @server.resource("/balance/{token}")
    async def get_token_balance(token: str) -> Decimal:
        """Get the token balance of the user's account."""

        from ape import convert
        from ape.types import AddressType

        token = Token.at(convert(token, AddressType))
        return token.balanceOf(account)  # type: ignore[attr-defined]

    @server.tool()
    async def get_price(ctx: Context, base: str, quote: str) -> Decimal:
        """Get the current price of BASE in terms of QUOTE."""
        uni = ctx.request_context.lifespan_context

        return uni.price(base, quote)

    @server.tool()
    async def swap(
        ctx: Context,
        have: str,
        want: str,
        amount_in: Decimal | None = None,
        max_amount_in: Decimal | None = None,
        amount_out: Decimal | None = None,
        min_amount_out: Decimal | None = None,
        slippage: Decimal | None = None,
    ) -> str:
        """Swap HAVE for WANT using the configured swap options."""

        uni = ctx.request_context.lifespan_context
        receipt = uni.swap(
            have=have,
            want=want,
            amount_in=amount_in,
            amount_out=amount_out,
            slippage=slippage,
            sender=account,
            confirmations_required=0,
        )
        return str(receipt.txn_hash)

    server.run(transport="http")
