"""
Модуль игр Tehnolog Games.
Централизованный RNG (crypto-safe), константы таймеров, единый интерфейс.
"""

from games.rng import game_random
from games.constants import GAME_MAX_DURATION_SEC

__all__ = ["game_random", "GAME_MAX_DURATION_SEC"]
