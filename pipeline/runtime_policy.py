"""Select server behavior before importing configuration or loading credentials.

Ordinary Wael datasets keep their existing online defaults. Prepared/imported
datasets are offline demonstrations even when the surrounding shell has API keys.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def configure_server_runtime(root: Path, environ=None) -> bool:
    """Return offline mode, setting its flag only for an explicitly offline scene."""
    env = os.environ if environ is None else environ
    # A hosted preview must never discover credentials or initialize online
    # services, even if the host inherited an explicit online setting.
    if env.get("VALMET_PUBLIC_DEMO") == "1" or env.get("FIELD_ATLAS_MCP_PUBLIC") == "1":
        env["VALMET_OFFLINE"] = "1"
        return True
    if env.get("VALMET_OFFLINE") == "1":
        return True
    data = Path(env.get("VALMET_DATA_DIR", Path(root) / "web" / "data")).expanduser()
    manifest = data / "digital_twin.json"
    try:
        twin = json.loads(manifest.read_text())
    except FileNotFoundError:
        return False  # keep original startup behavior when a scene is not built yet
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read scene runtime policy from {manifest}") from error
    if not isinstance(twin, dict):
        raise ValueError(f"Invalid scene manifest at {manifest}")
    runtime = twin.get("runtime") or {}
    scene = twin.get("scene") or {}
    offline = (
        runtime.get("offline") is True
        or runtime.get("external_models_enabled") is False
        or scene.get("membership_mode") == "exact-source-rows"
        or scene.get("dataset_kind") in {"wael-original-baseline", "field-atlas-import"}
        or any(asset.get("source_region_id") for asset in twin.get("assets", []))
    )
    if offline:
        env["VALMET_OFFLINE"] = "1"
    return offline
