# Overview

This SDK is used to develop the following bots targeting the Uniswap protocol on any supported networks

## Pricewatcher

The Pricewatcher bot will collect on-chain pricing information for the list of tokens given, against a [`QUOTE_TOKEN`](#quote_token).

### Configuration

#### `QUOTE_TOKEN`

_required_

The token symbol or address to use as the quote token when measuring the price of all [`TOKENS_TO_WATCH`](#tokens_to_watch).

#### `TOKENS_TO_WATCH`

_required_

A comma-separated list of token symbols and/or addresses to collect pricing info for.

> [!IMPORTANT]
> Must contain at least one token

#### `INTERMEDIATE_TOKENS`

_optional_

A comma-separated list of token symbols and/or addresses to use when indexing uniswap.
Defaults to the configured tokenlist.

#### `MEASUREMENT_CRON`

_optional_

A cron-spec used for triggering the token measurements.
Defaults to every minute on the minute (e.g. **:**:00).

### Metrics

#### `{base}/{quote}`

The price for the token `{base}` in terms of the token `{quote}`.

## Arbitrage

The Arbitrage bot will monitor for price divergences from the given reference price, and buy tokens until the market price is back in line with it.

> [!IMPORTANT]
> This bot is not designed nor intended for production use.
> It is meant to serve as an example that you can use when designing your own.

> [!NOTE]
> This bot can be used on public testnets in order to bring activity to Uniswap deployments that would otherwise not have any due to lack of economic incentives.

### Configuration

#### `TOKENA`

_required_

The first token symbol or address to perform arbitrage with.
Used as the quote token when measuring the market price [`price`](#price).

#### `TOKENB`

_required_

The second token symbol or address to perform arbitrage with.
Used as the base token when measuring the market price [`price`](#price).

#### `INTERMEDIATE_TOKENS`

_optional_

A comma-separated list of token symbols and/or addresses to use when indexing uniswap.
Defaults to the configured tokenlist.

#### `REFERENCE_PRICE`

_optional_

The reference price to use in order to compare to the [market price](#price) for arbitrage oppurtunities.
Defaults to a reference price of 1:1 (good for stablecoin pairs).

> [!IMPORTANT]
> Besides stablecoins, it is not a good idea in practice to use a
> static reference price to perform arbitrage.

#### `ARBITRAGE_THRESHOLD`

_optional_

The threshold of divergence of the [market price](#price) from the
[reference price](#reference_price) required to trigger an arbitrage.

Defaults to 2.5%.

#### `MAX_SWAP_SIZE_TOKENA`

_optional_

The maximum amount of [`TOKENA`](#tokena) to sell in any one trade.

Defaults to full inventory size.

#### `MAX_SWAP_SIZE_TOKENB`

_optional_

The maximum amount of [`TOKENB`](#tokena) to sell in any one trade.

Defaults to full inventory size.

`USE_PRIVATE_MEMPOOL`

_optional_

Whether to use a "private mempool" to submit the trade.
Requires configuring a supported provider through Ape.

Defaults to False.

> [!IMPORTANT]
> Certain networks leak public mempool activity to MEV searchers,
> who can sandwich your trades in order to extract value from it.
> Please use this bot carefully!

#### `MEASUREMENT_CRON`

_optional_

A cron-spec used for triggering the token measurements.
Defaults to every 5 minutes on the minute (e.g. **:**:05).

### Metrics

#### `{TOKENA.symbol()}`

The amount of inventory the bot account has for [`TOKENA`](#tokena).

#### `{TOKENB.symbol()}`

The amount of inventory the bot account has for [`TOKENB`](#tokenb).

#### `price`

The price of [`TOKENA`](#tokena) in terms of the [`TOKENB`](#tokenb).
