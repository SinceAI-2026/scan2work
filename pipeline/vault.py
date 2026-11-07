"""Local secrets vault: finds OPENAI_API_KEY without it ever living in code.

Lookup order (first hit wins, nothing is overwritten):
  1. an already-set environment variable
  2. macOS Keychain item  service="valmet-twin", account=<NAME>   (set via scripts/set_openai_key.py)
  3. valmet-twin/secrets.env   lines like  OPENAI_API_KEY=sk-...  (git-ignored)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SERVICE = "valmet-twin"
NAMES = ("OPENAI_API_KEY", "HF_TOKEN", "GEMINI_API_KEY")


def _keychain(name: str) -> str | None:
    if not shutil.which("security"):
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _env_file(path: Path) -> dict:
    vals = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    vals[k.strip()] = v
    except OSError:
        pass
    return vals


def load_secrets(root: Path) -> dict:
    """Populate os.environ from the vault. Returns {name: source} for logging (never the value)."""
    file_vals = _env_file(root / "secrets.env")
    found = {}
    for name in NAMES:
        if os.environ.get(name):
            found[name] = "environment"
        elif (v := _keychain(name)):
            os.environ[name] = v
            found[name] = "keychain"
        elif name in file_vals:
            os.environ[name] = file_vals[name]
            found[name] = "secrets.env"
    return found
