from __future__ import annotations

import hashlib
import secrets

from .core import AppError


class SeededRandom:
    """Stable cross-Python-version RNG, saved with every successful operation."""

    def __init__(self, state):
        self.state = state
        if not state["seed"]:
            state["seed"] = secrets.token_hex(32)
        self.draws = []

    def below(self, bound):
        if not 1 <= bound <= 10**12:
            raise AppError("invalid_random_bound", "Недопустимый диапазон случайного результата.", 422)
        cutoff = 2**256 - 2**256 % bound
        for _ in range(1024):
            counter = self.state["counter"]
            if counter >= 2**63 - 1:
                raise AppError("rng_exhausted", "Исчерпан счётчик RNG. Задайте новый seed в настройках.", 409)
            raw = hashlib.sha256(f"taverna/rng/v1/{self.state['seed']}/{counter}".encode()).digest()
            self.state["counter"] += 1
            value = int.from_bytes(raw, "big")
            if value < cutoff:
                result = value % bound
                self.draws.append({"counter": counter, "bound": bound, "value": result})
                return result
        raise AppError("rng_exhausted", "Не удалось получить ограниченный случайный результат.", 409)

    def dice(self, spec):
        rolls = [self.below(spec["sides"]) + 1 for _ in range(spec["count"])]
        return {"rolls": rolls, "modifier": spec["modifier"], "total": sum(rolls) + spec["modifier"]}
