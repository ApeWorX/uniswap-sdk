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
