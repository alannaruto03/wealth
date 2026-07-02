"""Polymarket 15-minute crypto "Up or Down" market bot.

Event-driven hybrid maker for the recurring short-horizon binary markets
(slug grid: ``btc-updown-15m-{unix_ts}``). Paper mode simulates fills against
the LIVE order book and settles on the real market outcome — real data, fake
money. See configs/polymarket.yaml and ``wealth poly run``.
"""
