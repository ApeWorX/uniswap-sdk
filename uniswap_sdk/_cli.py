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
