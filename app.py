"""
app.py  Point d'entrée du dashboard MKAN temps réel.

Modes de lancement (depuis m:\\Ecole\\Mémoire\\Modelisation) :
    python -m dashboard.app              # buffer en mémoire (défaut, sans dépendance)
    python -m dashboard.app --redis      # activer Redis via WSL2 (optionnel)
    python -m dashboard.app --debug      # mode debug Dash (hot-reload)
    python -m dashboard.app --port 8051  # port personnalisé
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import dash

from dashboard.inference import MKANInference
from dashboard.layout   import build_layout
from dashboard import callbacks, callbacks_extra

CHECKPOINT = _ROOT / "MKAN" / "checkpoints" / "mkan_final.pt"

# ── Parsing des arguments ─────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Dashboard MKAN  scoring fraude temps réel")
parser.add_argument("--no-redis", action="store_true",
                    help="Forcer le buffer en mémoire même si Redis est disponible")
parser.add_argument("--debug", action="store_true",
                    help="Mode debug Dash (hot-reload, messages d'erreur complets)")
parser.add_argument("--port",  type=int, default=8050, help="Port HTTP (défaut 8050)")
parser.add_argument("--host",  default="0.0.0.0",      help="Hôte (défaut 0.0.0.0)")
args, _ = parser.parse_known_args()

# ── Buffer : Redis par défaut, FallbackBuffer si absent ou --no-redis ─────────

if args.no_redis:
    from dashboard._fallback_buffer import FallbackBuffer
    buffer = FallbackBuffer(W=10)
    print("[OK] Buffer en mémoire (--no-redis)")
else:
    try:
        from dashboard.buffer import RedisWindowBuffer
        buffer = RedisWindowBuffer(host="localhost", port=6379, W=10, ttl=86400)
        buffer.r.ping()
        print("[OK] Redis connecté sur localhost:6379")
    except Exception as e:
        print(f"[WARN] Redis non disponible ({e})  basculement sur le buffer en mémoire")
        print("       Lancez Redis sur localhost:6379 pour activer la persistance.")
        from dashboard._fallback_buffer import FallbackBuffer
        buffer = FallbackBuffer(W=10)

# ── Modèle ────────────────────────────────────────────────────────────────────

try:
    inference = MKANInference(CHECKPOINT)
    print(f"[OK] Modèle chargé : {CHECKPOINT.name} "
          f"(hidden={inference.model.hidden_size}, "
          f"features={len(inference.feature_cols)})")
except FileNotFoundError:
    print(f"[ERR] Checkpoint introuvable : {CHECKPOINT}")
    print("      Placez mkan_final.pt dans MKAN/checkpoints/ et relancez.")
    sys.exit(1)

callbacks.init(buffer, inference)

# ── Flux live : consommation des transactions brutes MoMTSim ──────────────────

from dashboard import live_ingest

live_ingest.start("rest")
print(f"[OK] Ingestion live démarrée (REST {live_ingest.DEMO_ENDPOINTS['rest_url']})")
print("     Lancez le producteur : cd MOMTSIM && python run_live.py")

# ── Application Dash ──────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    title="MKAN  Scoring Fraude",
    suppress_callback_exceptions=True,
    assets_folder=str(_ROOT / "dashboard" / "assets"),
    update_title=None,
)
app.index_string = """
<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    <link rel="icon" type="image/png" href="/assets/logo_mkan.png">
    {%css%}
  </head>
  <body>
    {%app_entry%}
    <footer>
      {%config%}
      {%scripts%}
      {%renderer%}
    </footer>
  </body>
</html>
"""
app.layout = build_layout()
callbacks.register_callbacks(app)
callbacks_extra.register_extra(app, buffer=buffer, inference=inference)

# ── Lancement ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dashboard.buffer import RedisWindowBuffer as _RWB
    mode = "REDIS" if isinstance(buffer, _RWB) else "MÉMOIRE"
    print("\n" + "=" * 55)
    print(f" Dashboard MKAN [{mode}]")
    print(f" http://{args.host}:{args.port}")
    print("=" * 55 + "\n")
    app.run(debug=args.debug, host=args.host, port=args.port)
