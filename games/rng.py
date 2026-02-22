"""
Централизованный RNG для игр Tehnolog Games.
Использует secrets (crypto-safe). Формулы и шансы не раскрываются игроку.
"""

import secrets

# Один общий генератор на основе secrets для всех игр
_system_random = secrets.SystemRandom()


class GameRandom:
    """Обёртка над crypto-safe RNG для игровой логики."""

    @staticmethod
    def random() -> float:
        """Случайное float в [0.0, 1.0)."""
        return _system_random.random()

    @staticmethod
    def uniform(a: float, b: float) -> float:
        """Случайное float в [a, b] (или [a, b) в зависимости от округления)."""
        return _system_random.uniform(a, b)

    @staticmethod
    def randint(a: int, b: int) -> int:
        """Случайное int в [a, b] включительно."""
        return _system_random.randint(a, b)

    @staticmethod
    def choice(seq):
        """Случайный элемент последовательности."""
        return _system_random.choice(seq)

    @staticmethod
    def shuffle(seq):
        """Перемешать последовательность на месте."""
        _system_random.shuffle(seq)

    @staticmethod
    def choices(population, weights=None, *, k=1):
        """Взвешенная выборка k элементов (для интервалов обвала и т.п.)."""
        return _system_random.choices(population, weights=weights, k=k)


game_random = GameRandom()
