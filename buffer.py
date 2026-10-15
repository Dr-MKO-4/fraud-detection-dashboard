"""
buffer.py  RedisWindowBuffer : fenêtre glissante W=10 par compte dans Redis.

Clé Redis : window:{account_id}  →  liste de W JSON strings, TTL = 24h
"""

from __future__ import annotations

import json
import numpy as np


class RedisWindowBuffer:
    """
    Maintient les W dernières transactions de chaque compte dans Redis.

    Args:
        host : hôte Redis (défaut "localhost")
        port : port Redis (défaut 6379)
        W    : taille de la fenêtre glissante (défaut 10)
        ttl  : durée de vie en secondes (défaut 86400 = 24h)
    """

    def __init__(self, host: str = "localhost", port: int = 6379,
                 W: int = 10, ttl: int = 86400):
        import redis
        self.W   = W
        self.ttl = ttl
        # protocol=2 (RESP2)  évite le handshake HELLO (RESP3), absent des
        # builds Redis < 6.0 comme Redis-x64-3.0.504 (voir LANCEMENT.md).
        self.r   = redis.Redis(host=host, port=port, decode_responses=True,
                               protocol=2)

    # ── clés Redis ────────────────────────────────────────────────────────────

    _ACCOUNTS_KEY = "accounts:active"

    def _key(self, account_id: str) -> str:
        return f"window:{account_id}"

    def _hist_key(self, account_id: str) -> str:
        return f"history:{account_id}"

    # ── opérations de fenêtre ─────────────────────────────────────────────────

    def push(self, account_id: str, features: list[float]) -> np.ndarray | None:
        """
        Ajoute une transaction. Retourne la fenêtre complète (W, F) si pleine,
        None sinon. Utilise un pipeline pour réduire les round-trips Redis à 1.
        """
        key = self._key(account_id)
        pipe = self.r.pipeline()
        pipe.rpush(key, json.dumps(features))
        pipe.expire(key, self.ttl)
        pipe.ltrim(key, -self.W, -1)
        pipe.llen(key)
        # Maintient un SET des comptes actifs  évite le KEYS O(N) de list_accounts()
        pipe.sadd(self._ACCOUNTS_KEY, account_id)
        results = pipe.execute()
        if results[3] == self.W:
            return self._load(key)
        return None

    def push_batch(self, accounts: list[str],
                   features_list: list[list[float]]) -> dict[str, np.ndarray]:
        """
        Pousse tout un lot de transactions (un même `step` temporel) en 2
        allers-retours réseau au total, quel que soit N :
          1. un seul pipeline pour tous les rpush/expire/ltrim/llen/sadd,
          2. un second pipeline pour rapatrier les fenêtres désormais pleines.
        Si un compte apparaît plusieurs fois dans le lot, seule sa DERNIÈRE
        occurrence sert à décider si sa fenêtre est complète (état du compte
        à la fin de ce pas de temps, pas transaction par transaction).

        Returns: {account_id: fenêtre (W, F)} pour les comptes complets.
        """
        if not accounts:
            return {}
        pipe = self.r.pipeline()
        last_pos: dict[str, int] = {}
        for i, acc in enumerate(accounts):
            key = self._key(acc)
            pipe.rpush(key, json.dumps(features_list[i]))
            pipe.expire(key, self.ttl)
            pipe.ltrim(key, -self.W, -1)
            pipe.llen(key)
            pipe.sadd(self._ACCOUNTS_KEY, acc)
            last_pos[acc] = i   # écrasé si acc réapparaît  garde la dernière occurrence
        results = pipe.execute()
        complete = [acc for acc, i in last_pos.items() if results[i * 5 + 3] == self.W]
        if not complete:
            return {}
        pipe2 = self.r.pipeline()
        for acc in complete:
            pipe2.lrange(self._key(acc), 0, -1)
        raw_windows = pipe2.execute()
        return {acc: np.array([json.loads(v) for v in raw], dtype=np.float32)
                for acc, raw in zip(complete, raw_windows)}

    def get_window(self, account_id: str) -> np.ndarray | None:
        key  = self._key(account_id)
        if self.r.llen(key) < self.W:
            return None
        return self._load(key)

    def get_fill_level(self, account_id: str) -> int:
        return int(self.r.llen(self._key(account_id)))

    def get_fill_levels(self, account_ids: list[str]) -> dict[str, int]:
        """Batch llen via pipeline  1 round-trip au lieu de N."""
        if not account_ids:
            return {}
        pipe = self.r.pipeline()
        for a in account_ids:
            pipe.llen(self._key(a))
        results = pipe.execute()
        return {a: int(n) for a, n in zip(account_ids, results)}

    def flush(self, account_id: str) -> None:
        pipe = self.r.pipeline()
        pipe.delete(self._key(account_id))
        pipe.srem(self._ACCOUNTS_KEY, account_id)
        pipe.execute()

    def count_accounts(self) -> int:
        """Nombre de comptes actifs, en O(1) côté Redis (SCARD)."""
        return int(self.r.scard(self._ACCOUNTS_KEY))

    def list_accounts(self) -> list[str]:
        """O(1) côté serveur  lit le SET maintenu par push()/flush(), pas de KEYS."""
        return list(self.r.smembers(self._ACCOUNTS_KEY))

    # ── historique des scores ─────────────────────────────────────────────────

    def push_score(self, account_id: str, score: float) -> None:
        key = self._hist_key(account_id)
        pipe = self.r.pipeline()
        pipe.rpush(key, score)
        pipe.ltrim(key, -50, -1)
        pipe.expire(key, self.ttl)
        pipe.execute()

    def get_scores(self, account_id: str, n: int = 50) -> list[float]:
        key = self._hist_key(account_id)
        raw = self.r.lrange(key, -n, -1)
        return [float(v) for v in raw]

    # ── dérive globale ────────────────────────────────────────────────────────

    def push_drift_score(self, score: float) -> None:
        pipe = self.r.pipeline()
        pipe.rpush("drift:scores", score)
        pipe.ltrim("drift:scores", -2000, -1)
        # Compteur monotone  la longueur de drift:scores se fige une fois les
        # 2000 éléments atteints (LTRIM), donc seule cette version distingue
        # "rien de nouveau" de "la fenêtre a tourné" pour l'invalidation de cache.
        pipe.incr("drift:version")
        pipe.execute()

    def get_drift_scores(self) -> list[float]:
        raw = self.r.lrange("drift:scores", 0, -1)
        return [float(v) for v in raw]

    def get_drift_version(self) -> int:
        return int(self.r.get("drift:version") or 0)

    def clear_drift(self) -> None:
        pipe = self.r.pipeline()
        pipe.delete("drift:scores")
        pipe.incr("drift:version")
        pipe.execute()

    def flush_all(self) -> None:
        """Vide toutes les fenêtres, historiques de scores et la dérive.

        Balaye les clés par motif (SCAN) plutôt que par le seul ensemble des comptes
        actifs : des fenêtres laissées par une session précédente doivent disparaître
        même si elles n'y figurent plus."""
        for pattern in ("window:*", "history:*"):
            batch = []
            for key in self.r.scan_iter(match=pattern, count=5000):
                batch.append(key)
                if len(batch) >= 5000:
                    self.r.delete(*batch)
                    batch = []
            if batch:
                self.r.delete(*batch)
        self.r.delete(self._ACCOUNTS_KEY)
        self.clear_drift()

    # ── interne ───────────────────────────────────────────────────────────────

    def _load(self, key: str) -> np.ndarray:
        rows = self.r.lrange(key, 0, -1)
        return np.array([json.loads(r) for r in rows], dtype=np.float32)
