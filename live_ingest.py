"""
live_ingest.py  Consommation du flux de transactions BRUTES.

Le dashboard ne reçoit pas de variables précalculées : il consomme des
transactions naturelles (montants, soldes, acteurs, action) exactement comme le
ferait un branchement sur un flux opérateur, puis calcule lui-même les 12
variables du modèle, de façon incrémentale (dashboard/stream_features.py).

Deux transports, un seul actif à la fois :
  - rest  : GET <url>?since_id=N sur un producteur ;
  - kafka : un topic Kafka.
Les points de connexion sont modifiables (mode live : API ou connecteur de
l'opérateur, saisis à la main) ; le mode démo pointe vers MoMTSim.

Le thread de fond ne touche jamais aux stores Dash : il dépose les lots prêts à
scorer dans une file, vidée par le tick du callback principal.

Échauffement : les lignes marquées `warmup` (historique que le producteur
rejoue avant le direct) alimentent l'état des variables et préremplissent les
fenêtres, mais ne produisent aucune décision.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque, defaultdict

from .stream_features import IncrementalFeatures

DEMO_ENDPOINTS = {
    "rest_url": "http://127.0.0.1:8766/live/transactions",
    "kafka_bootstrap": "localhost:9092",
    "kafka_topic": "mm.transactions.momtsim",
    "rest_token": "",
}

REST_POLL_SECONDS = 0.5
REST_PAGE = 20000
STEP_IDLE_SECONDS = 2.0     # un step est considéré complet après ce silence
_PENDING_MAX = 300

_pending: "queue.Queue[tuple]" = queue.Queue()
_thread: threading.Thread | None = None
_stop = threading.Event()
_endpoints: dict = dict(DEMO_ENDPOINTS)
_state = {"connected": False, "source": None, "received": 0, "error": None,
          "endpoint": "", "warmup_rows": 0, "run_id": None, "manual": False}
_state_lock = threading.Lock()
_recent: "deque[tuple[float, int]]" = deque()      # (instant, nb reçus) sur ~10 s
_RATE_WINDOW_SECONDS = 10.0

# état du calcul (propre au flux courant)
_engine = IncrementalFeatures()
_steps: dict[int, list] = {}
_max_seen = -1
_last_row_time = 0.0
_prefill: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
_cur_run = None
_on_reset = None          # fonction appelée à chaque nouveau flux (vidage du buffer)


def set_reset_hook(fn) -> None:
    global _on_reset
    _on_reset = fn


def endpoints() -> dict:
    return dict(_endpoints)


def stats() -> dict:
    """État du flux, avec le débit réellement mesuré (transactions/s, moyenne 10 s)."""
    now = time.perf_counter()
    with _state_lock:
        while _recent and now - _recent[0][0] > _RATE_WINDOW_SECONDS:
            _recent.popleft()
        out = dict(_state)
        span = (now - _recent[0][0]) if _recent else 0.0
        out["rate"] = (sum(n for _, n in _recent) / max(span, 1.0)) if _recent else 0.0
    return out


def received_count() -> int:
    with _state_lock:
        return _state["received"]


def _mark(**kwargs) -> None:
    with _state_lock:
        _state.update(kwargs)


def _count(n: int) -> None:
    with _state_lock:
        _state["received"] += n
        _recent.append((time.perf_counter(), n))


def pop_pending() -> tuple[list[str], list[list[float]], list[dict], int | None]:
    """Vide la file : tout ce qui est arrivé depuis le tick précédent."""
    accounts: list[str] = []
    features: list[list[float]] = []
    meta: list[dict] = []
    step_val: int | None = None
    while True:
        try:
            acc, feat, met, step = _pending.get_nowait()
        except queue.Empty:
            break
        accounts.extend(acc)
        features.extend(feat)
        meta.extend(met)
        step_val = step
    return accounts, features, meta, step_val


# ── Calcul des variables, step par step ───────────────────────────────────────

def _reset_stream() -> None:
    """Nouveau flux (producteur redémarré, retour en arrière) : on repart de zéro."""
    global _engine, _steps, _max_seen, _prefill, _cur_run
    # Les fenêtres du buffer (Redis les conserve entre les sessions) proviennent du
    # flux précédent : les identifiants de comptes sont réutilisés à chaque run du
    # producteur, donc des vecteurs d'un ancien run contamineraient les nouvelles
    # décisions. Elles doivent disparaître avec l'état des variables.
    if _on_reset is not None:
        try:
            _on_reset()
        except Exception as exc:                      # ne jamais bloquer l'ingestion
            _mark(error=f"vidage du buffer impossible : {exc}")
    _engine = IncrementalFeatures()
    _steps = {}
    _max_seen = -1
    _cur_run = None
    _prefill = defaultdict(lambda: deque(maxlen=10))
    while not _pending.empty():
        try:
            _pending.get_nowait()
        except queue.Empty:
            break


def _receive(rows: list[dict]) -> None:
    """Encaisse les transactions reçues (rapide, non bloquant)."""
    global _max_seen, _last_row_time, _cur_run
    if not rows:
        return
    # Détection du changement de flux LIGNE PAR LIGNE : un lot Kafka peut contenir la
    # fin d'un ancien run puis le début du suivant (identifiant de run différent, ou
    # step qui revient en arrière). On repart alors de zéro.
    for r in rows:
        s = int(r["step"])
        rid = r.get("run_id")
        if _max_seen >= 0 and (rid != _cur_run or s < _max_seen - 5):
            _reset_stream()
            _mark(warmup_rows=0)
        _cur_run = rid
        _steps.setdefault(s, []).append(r)
        if s > _max_seen:
            _max_seen = s
    _last_row_time = time.perf_counter()
    with _state_lock:
        _state["run_id"] = _cur_run


def _flush_prefill(step: int) -> None:
    """Fin de l'échauffement : préremplit les fenêtres avec les 10 derniers
    vecteurs de chaque compte, sans produire de décision."""
    if not _prefill:
        return
    accounts, matrix, meta = [], [], []
    for acc, vecs in _prefill.items():
        for v in vecs:
            accounts.append(acc)
            matrix.append(v)
            meta.append({"nameOrig": acc, "step": step, "isFraud": None,
                         "fraudScenario": None, "warmup": True})
    _prefill.clear()
    _pending.put((accounts, matrix, meta, step))


def _emit_step(step: int, rows: list[dict]) -> None:
    feats = _engine.update_step(step, rows)
    live_acc, live_mat, live_meta = [], [], []
    for r, f in zip(rows, feats):
        acc = str(r["nameOrig"])
        if r.get("warmup"):
            _prefill[acc].append(f)
            continue
        live_acc.append(acc)
        live_mat.append(f)
        live_meta.append({"nameOrig": acc, "step": step, "isFraud": r.get("isFraud"),
                          "fraudScenario": r.get("fraudScenario"), "warmup": False})
    if live_acc:
        _flush_prefill(step)
        while _pending.qsize() >= _PENDING_MAX:      # file bornée (scoring suspendu)
            try:
                _pending.get_nowait()
            except queue.Empty:
                break
        _pending.put((live_acc, live_mat, live_meta, step))


def _drain(force: bool = False) -> None:
    """Traite les steps complets : ceux qui précèdent le plus récent, ou tous si
    le flux est silencieux depuis STEP_IDLE_SECONDS."""
    if not _steps:
        return
    idle = force or (time.perf_counter() - _last_row_time) > STEP_IDLE_SECONDS
    for s in sorted(k for k in _steps if idle or k < _max_seen):
        _emit_step(s, _steps.pop(s))


# ── Transports ────────────────────────────────────────────────────────────────

def _run_rest() -> None:
    import urllib.request

    since_id = 0
    url = _endpoints["rest_url"]
    headers = {"Accept": "application/json"}
    if _endpoints.get("rest_token"):
        headers["Authorization"] = f"Bearer {_endpoints['rest_token']}"
    while not _stop.is_set():
        got = 0
        try:
            req = urllib.request.Request(f"{url}?since_id={since_id}&limit={REST_PAGE}",
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            _mark(connected=True, error=None)
            run_id = payload.get("run_id")
            with _state_lock:
                prev = _state["run_id"]
            if run_id is not None and prev not in (None, run_id):
                since_id = 0            # producteur redémarré : ses identifiants repartent de 1
                _reset_stream()
                _mark(run_id=run_id)
                continue
            rows = payload.get("transactions", [])
            got = len(rows)
            since_id = payload.get("last_id", since_id)
            if rows:
                for r in rows:
                    r.setdefault("run_id", run_id)
                warm = sum(1 for r in rows if r.get("warmup"))
                if warm:
                    _mark(warmup_rows=_state["warmup_rows"] + warm)
                _count(got)
                _receive(rows)
        except Exception as exc:
            _mark(connected=False, error=str(exc))
        _drain()
        if got < REST_PAGE:                 # rattrapé : on attend avant de relancer
            _stop.wait(REST_POLL_SECONDS)


def _run_kafka() -> None:
    try:
        from kafka import KafkaConsumer
    except ImportError:
        _mark(connected=False, error="kafka-python non installé")
        return
    try:
        consumer = KafkaConsumer(
            _endpoints["kafka_topic"],
            bootstrap_servers=_endpoints["kafka_bootstrap"],
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            fetch_max_bytes=16 * 1024 * 1024,
            max_partition_fetch_bytes=8 * 1024 * 1024,
        )
        _mark(connected=True, error=None)
    except Exception as exc:
        _mark(connected=False, error=str(exc))
        return

    # poll() rend la main au bout de 0,5 s au plus, même si des messages arrivent en
    # continu. L'ancienne boucle « for msg in consumer » ne se terminait qu'après 20 000
    # messages ou 1 s de silence : au débit live (190 messages toutes les 0,5 s) le
    # silence n'arrive jamais, et les données n'étaient livrées que par rafales d'une
    # cinquantaine de secondes.
    while not _stop.is_set():
        try:
            polled = consumer.poll(timeout_ms=500, max_records=20000)
            _mark(connected=True, error=None)
        except Exception as exc:
            _mark(connected=False, error=str(exc))
            _stop.wait(1.0)
            continue
        batch = [rec.value for recs in polled.values() for rec in recs]
        if batch:
            _count(len(batch))
            _mark(warmup_rows=_state["warmup_rows"] + sum(1 for r in batch if r.get("warmup")))
            _receive(batch)
        _drain()
    consumer.close()


def start(source: str = "rest", endpoints: dict | None = None) -> None:
    """(Re)démarre le thread de consommation sur le transport et les points de
    connexion demandés (par défaut : ceux de la démo MoMTSim)."""
    global _thread, _endpoints
    stop()
    _endpoints = {**DEMO_ENDPOINTS, **(endpoints or {})}
    _stop.clear()
    _reset_stream()
    with _state_lock:
        _recent.clear()
    target_label = (f"kafka://{_endpoints['kafka_bootstrap']}/{_endpoints['kafka_topic']}"
                    if source == "kafka" else _endpoints["rest_url"])
    _mark(connected=False, source=source, error=None, received=0, warmup_rows=0,
          run_id=None, endpoint=target_label, manual=endpoints is not None)
    _thread = threading.Thread(target=_run_kafka if source == "kafka" else _run_rest,
                               daemon=True)
    _thread.start()


def stop() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        _stop.set()
        _thread.join(timeout=3)
    _thread = None
    _mark(connected=False)


def disconnect() -> None:
    """Coupe le flux et oublie le point de connexion (mode live avant saisie)."""
    stop()
    _reset_stream()
    _mark(source=None, endpoint="", error=None, received=0, warmup_rows=0, run_id=None,
          manual=False)


def demo_label(source: str) -> str:
    """Libellé du point de connexion de démo pour ce transport."""
    d = DEMO_ENDPOINTS
    return f"kafka://{d['kafka_bootstrap']}/{d['kafka_topic']}" if source == "kafka" else d["rest_url"]
