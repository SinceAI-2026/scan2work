"""Central configuration for the Valmet Industrial Twin pipeline."""
import os as _os
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]

from .vault import load_secrets  # noqa: E402
OFFLINE = os.environ.get("VALMET_OFFLINE", "0") == "1"
SECRETS_FOUND = {} if OFFLINE else load_secrets(ROOT)
DATA_DIR = ROOT.parent / "Valmet_SinceAI2026 _1_"
PLY_PATH = ROOT.parent / "Equipment Room.ply"
XLSX_PATH = DATA_DIR / "Valmet_SinceAI_2026_Synthetic_Industrial_Metaverse_Builder_Pack (1) (2) (1).xlsx"
OUT_DIR = Path(os.environ.get("VALMET_DATA_DIR", ROOT / "web" / "data")).expanduser().resolve()

# --- Ingestion -------------------------------------------------------------
COORD_LIMIT_M = 60.0          # anything beyond is a corrupted record
BLOCK_SIZE = 2000             # rows per integrity block
BLOCK_VALID_RATIO = 0.9       # block considered intact above this ratio

# --- Perception ------------------------------------------------------------
GEOM_MIN_OPACITY = 0.25
GEOM_MAX_SCALE = 0.16         # filter blurry radiance fog splats
FLOOR_BAND = 0.08             # m above floor plane treated as floor
OVERHEAD_SPLIT_Y = 1.72       # height separating wall/floor assets from ceiling manifold
CEILING_Y_MIN = 1.97          # ceiling slab level
BACK_WALL_Z = 3.38            # back brick wall plane Z
VOXEL = 0.025
DBSCAN_EPS = 0.06
DBSCAN_MIN = 8
SPLIT_MAX_EXTENT = 0.95       # clusters larger than this are re-segmented into components
SPLIT_MIN_EPS = 0.028
MIN_CLUSTER_PTS = 18
MIN_CLUSTER_EXTENT = 0.07

# Which segmenter produces the objects (README "Two pipelines"):
#   heuristic - perception.py colour/geometry clustering, named by Gemini or the Decisions API (or their cached answers)
#   local     - local_models/: SAM 2.1 + SigLIP 2 + DINOv2 + OWLv2, open weights on this machine, no API key
SEGMENTER = _os.environ.get("VALMET_SEGMENTER", "heuristic")
LOCAL_OBJECTS_DIR = Path(_os.environ.get("VALMET_LOCAL_OBJECTS", ROOT / "local_models" / "data" / "pipeline"))

# --- Navigation ------------------------------------------------------------
GRID_RES = 0.05
OBST_MIN_H = 0.16             # ignore floor grating / cables below this
OBST_MAX_H = 1.62             # avatar clearance height (below overhead pipe rack)
OBST_MIN_HITS = 6             # solid splat count per 5cm cell to mark obstacle
ROBOT_RADIUS = 0.15
STANDOFF = 0.52               # inspection stand-off distance from target box
PROXIMITY_RADIUS = 2.5 
WALK_SPEED = 0.50             # m/s

# --- Knowledge / mapping -----------------------------------------------------
TARGET_ASSET_ID = "ASSET-003"  # CV-003: degraded, POSITION_DEVIATION, urgency high
# Physical object that plays CV-003: the motorised control valve (orange actuator on a white valve body) on the
# south-east wall, verified in the real walkthrough video (web/data/photos/OBJ-206.jpg). The brief allows any
# mapping; this one makes "position tracking deviation" an actuator fault you can see in the photo.
# (The earlier anchor at (2.9, 0.7, 2.7) came from blurry splat renders; the video showed it is a cardboard box.)
TARGET_ANCHOR = (2.12, 1.06, -1.60)
TARGET_ZONE = ((1.95, 0.80, -1.85), (2.45, 1.40, -1.30))   # (min xyz, max xyz): never dropped/merged/relabelled movable
RANDOM_SEED = 2026

# --- AI services (all optional; pipeline falls back to deterministic logic) ---
DECISIONS_MODEL = "gpt-6-luna"           # OpenAI Decisions API, needs OPENAI_API_KEY
DECISIONS_LABEL_TIER = ("equipment", "overhead")    # which extracted clusters get image-based relabeling
DECISIONS_WORKERS = 8
EMBED_MODEL = "google/embeddinggemma-300m"  # sentence-transformers id; TF-IDF fallback if absent
RAG_TOP_K = 4

# Object labelling backend: "auto" = Gemini if GEMINI_API_KEY is set, else the Decisions API.
import os as _os
LABELLER = _os.environ.get("VALMET_LABELLER", "auto")   # auto | gemini | decisions
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_BATCH = int(_os.environ.get("VALMET_GEMINI_BATCH", 20))   # renders per labelling request (paid tier)
GEMINI_MIN_INTERVAL_S = float(_os.environ.get("VALMET_GEMINI_INTERVAL", 1.5))   # free tier needs 13 (5 req/min)
GEMINI_WORKERS = int(_os.environ.get("VALMET_GEMINI_WORKERS", 4))   # parallel requests (paid tier); free tier: 1
GEMINI_DET_BATCH = 7         # images per detection request (42 video keyframes -> 6 requests)
DETECT_SOURCE = _os.environ.get("VALMET_DETECT_SOURCE", "video")   # video | renders
DETECT_TILES = _os.environ.get("VALMET_DETECT_TILES", "1") == "1"   # also detect on 4 zoomed quarters per keyframe
GEMINI_PHOTO_BATCH = int(_os.environ.get("VALMET_GEMINI_BATCH", 20))   # real-photo crops per request (smaller = more careful)
# Which images the AI labeller sees: 'photo' = real walkthrough frames where available (then splat renders),
# 'render' = splat renders only. tests/ab_photos.py measures both on the hand-labelled sample.
LABEL_IMAGES = _os.environ.get("VALMET_LABEL_IMAGES", "collage")   # render | photo | both | collage (tests/ab_photos.py)
GEMINI_COLLAGE_BATCH = 10    # 640x640 four-view collages per labelling request
