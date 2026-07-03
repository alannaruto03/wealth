"""Polymarket short-term BTC Up/Down trading bot.

A parallel stack to the OHLCV portfolio bot: binary event markets with limit
prices and hard expiries don't fit the weight-rebalancing Strategy/Broker
abstractions, so this subpackage has its own seam (PolymarketExecutor) while
reusing the shared Journal, metrics, and reporting.
"""
