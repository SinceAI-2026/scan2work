"""Store your OpenAI API key in the vault (macOS Keychain; falls back to secrets.env).

Run from valmet-twin/:   ./.venv/bin/python scripts/set_openai_key.py
The key is typed hidden and never printed. Re-run any time to replace it.
"""
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.vault import SERVICE  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "OPENAI_API_KEY"
key = getpass.getpass(f"Paste {name} (hidden): ").strip()
if not key:
    sys.exit("Nothing entered; vault unchanged.")

if shutil.which("security"):
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", key],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"Saved {name} to macOS Keychain (service '{SERVICE}').")
        sys.exit(0)
    print(f"Keychain write failed ({r.stderr.strip()}); using secrets.env instead.")

env = ROOT / "secrets.env"
lines = [l for l in (env.read_text().splitlines() if env.exists() else []) if not l.startswith(f"{name}=")]
lines.append(f"{name}={key}")
env.write_text("\n".join(lines) + "\n")
os.chmod(env, 0o600)
print(f"Saved {name} to {env} (owner-only permissions).")
