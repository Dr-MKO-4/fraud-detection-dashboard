"""
_fallback_buffer.py  Buffer en mémoire si Redis n'est pas disponible.
Comportement identique à RedisWindowBuffer mais sans persistance.
"""

from __future__ import annotations

import json
from collections import deque
import numpy as np


class FallbackBuffer:
    """Buffer en mémoire (deque)  se vide au redémarrage."""

    def __init__(self, W: int = 10):
        self.W        = W
        self._windows : dict[str, deque]       = {}
        self._history : dict[str, deque]       = {}  # deque(maxlen=50) par compte
        self._drift   : deque                  = deque(maxlen=2000)
        self._drift_version = 0

    def _key(self, acc: str) -> deque:
        if acc not in self._windows:
            self._windows[acc] = deque(maxlen=self.W)
        return self._windows[acc]

    def push(self, account_id: str, features: list[float]) -> np.ndarray | None:
        dq = self._key(account_id)
        dq.append(features)
        if len(dq) == self.W:
            return np.array(list(dq), dtype=np.float32)
        return None

    def push_batch(self, accounts: list[str],
                   features_list: list[list[float]]) -> dict[str, np.ndarray]:
        """Équivalent en mémoire de RedisWindowBuffer.push_batch : ne garde que
        l'état de fin de lot par compte (pas d'aller-retour réseau ici, mais
        même sémantique pour rester cohérent entre les deux backends)."""
        last_idx: dict[str, int] = {}
        for i, acc in enumerate(accounts):
            self._key(acc).append(features_list[i])
            last_idx[acc] = i
        return {acc: np.array(list(self._windows[acc]), dtype=np.float32)
                for acc in last_idx if len(self._windows[acc]) == self.W}

    def get_window(self, account_id: str) -> np.ndarray | None:
        dq = self._key(account_id)
        if len(dq) < self.W:
            return None
        return np.array(list(dq), dtype=np.float32)

    def get_fill_level(self, account_id: str) -> int:
        dq = self._windows.get(account_id)          # ne crée pas d'entrée pour un compte inconnu
        return len(dq) if dq else 0

    def get_fill_levels(self, account_ids: list[str]) -> dict[str, int]:
        return {a: self.get_fill_level(a) for a in account_ids}

    def clear_drift(self) -> None:
        self._drift.clear()
        self._drift_version += 1

    def flush_all(self) -> None:
        """Vide toutes les fenêtres, historiques de scores et la dérive."""
        self._windows.clear()
        self._history.clear()
        self.clear_drift()

    def flush(self, account_id: str) -> None:
        self._windows[account_id] = deque(maxlen=self.W)

    def count_accounts(self) -> int:
        return len(self._windows)

    def list_accounts(self) -> list[str]:
        return list(self._windows.keys())

    def push_score(self, account_id: str, score: float) -> None:
        if account_id not in self._history:
            self._history[account_id] = deque(maxlen=50)
        self._history[account_id].append(score)

    def get_scores(self, account_id: str, n: int = 50) -> list[float]:
        dq = self._history.get(account_id)
        if not dq:
            return []
        lst = list(dq)
        return lst[-n:] if n < len(lst) else lst

    def push_drift_score(self, score: float) -> None:
        self._drift.append(score)
        self._drift_version += 1

    def get_drift_scores(self) -> list[float]:
        return list(self._drift)

    def get_drift_version(self) -> int:
        return self._drift_version

    # Compatibilité avec le code qui accède à buffer.r (Redis client)
    class _NoRedis:
        def ping(self): raise RuntimeError("FallbackBuffer  pas de Redis")
    r = _NoRedis()
