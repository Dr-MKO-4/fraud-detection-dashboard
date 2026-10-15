"""
users.py  Gestion simple des comptes utilisateur (JSON, sans base externe).

Compte préchargé au premier démarrage :
  email    : voir ci-dessous
  password : demo1234
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_STORE = Path(__file__).parent / "users.json"

_DEFAULT_USER = {
    "id": "U001",
    "name": "MOUKOKO EKAMBI Georges Miguel",
    "role": "Analyste risque  mémoire ENSPD",
    "email": "georgesmoukoko4@gmail.com",
}
_DEFAULT_PASSWORD = "demo1234"
# Ancienne adresse  migration automatique au chargement
_LEGACY_EMAIL = "ayunirodrick9@gmail.com"


def _hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode("utf-8")).hexdigest()


def _load() -> list[dict]:
    if _STORE.exists():
        try:
            users = json.loads(_STORE.read_text("utf-8"))
            # Migration : corriger l'ancien email si présent
            changed = False
            for u in users:
                if u.get("id") == "U001" and u.get("email") == _LEGACY_EMAIL:
                    u["email"] = _DEFAULT_USER["email"]
                    changed = True
            if changed:
                _STORE.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")
            return users
        except Exception:
            pass
    seed = {**_DEFAULT_USER, "password": _hash(_DEFAULT_PASSWORD)}
    _STORE.write_text(json.dumps([seed], ensure_ascii=False, indent=2), "utf-8")
    return [seed]


def _save(users: list[dict]) -> None:
    _STORE.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")


def _public(u: dict) -> dict:
    return {k: v for k, v in u.items() if k != "password"}


def authenticate(email: str, password: str) -> dict | None:
    for u in _load():
        if u["email"] == email and u["password"] == _hash(password):
            return _public(u)
    return None


def create_user(name: str, email: str, password: str,
                role: str = "Analyste") -> tuple[dict | None, str | None]:
    users = _load()
    if any(u["email"] == email for u in users):
        return None, "Cet email est déjà utilisé."
    uid = f"U{len(users) + 1:03d}"
    u = {"id": uid, "name": name, "role": role, "email": email, "password": _hash(password)}
    users.append(u)
    _save(users)
    return _public(u), None
