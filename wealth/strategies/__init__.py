"""Strategy package — importing it registers all built-in strategies."""
from wealth.strategies.base import (
    Strategy,
    register,
    get_strategy,
    available_strategies,
    STRATEGY_REGISTRY,
)

# Import concrete strategies for their @register side effects.
from wealth.strategies import trend_breakout  # noqa: F401
from wealth.strategies import dual_momentum  # noqa: F401
from wealth.strategies import mean_reversion  # noqa: F401
from wealth.strategies import grid  # noqa: F401
from wealth.strategies import funding_arb  # noqa: F401

__all__ = [
    "Strategy",
    "register",
    "get_strategy",
    "available_strategies",
    "STRATEGY_REGISTRY",
]
