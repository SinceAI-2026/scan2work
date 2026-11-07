#!/usr/bin/env python3
"""Build portable release archives without modifying input datasets or calling AI.

Archives are flat-root tar.gz files. manifest.json is consumed by install.py;
asset-inventory.json inside each archive describes every other archived file.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = {
    "digital_twin.json", "boiler_room_points.bin", "splat_instance.bin",
    "source_scan.ply", "source_members.npz", "splat_source_rows.npy",
    "floorplan_scan.png", "navigation_map_preview.png", "overlay_walk.mp4",
    "walk_poses.json", "rag_index.json", "rag_embeddings.npy",
    "baseline_report.json", "import_report.json", "derived_context.json",
    "context_recovery_report.json",
}
DATA_DIRS = {"photos", "walk_frames", "proposal-review", "object-rebuild", "scene-extraction", "library", "take_apart"}
DATA_SUFFIXES = {".json", ".bin", ".ply", ".npy", ".npz", ".jpg", ".jpeg", ".png", ".mp4", ".webm", ".gspl", ".glb"}
REQUIRED_DATA = {
    "digital_twin.json", "boiler_room_points.bin", "splat_instance.bin",
    "source_scan.ply", "source_members.npz", "splat_source_rows.npy", "rag_index.json",
}
CODE_DIRS = {"pipeline", "scripts", "tests", "web", "docs", "distribution", "live", "arduino"}
CODE_FILES = {"server.py", "mcp_server.py", "README.md", "AGENTS.md", "requirements.txt", "requirements-import.txt", "requirements-demo.lock.txt"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def portable_json(value):
    """Only remove developer-machine review endpoints; preserve geometry/evidence."""
    if isinstance(value, dict):
        return {k: (None if k in {"full_view_url", "full_proposal_view_url"} else portable_json(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [portable_json(v) for v in value]
    return value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def data_entries(folder):
    folder = Path(folder).resolve()
    for name in REQUIRED_DATA:
        if not (folder / name).is_file():
            raise ValueError(f"Dataset missing required file: {name}")
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if relative.parts[0] not in DATA_FILES | DATA_DIRS:
            continue
        if path.is_symlink():
            raise ValueError(f"Dataset symlink is not portable: {relative}")
        if not path.is_file() or path.suffix.lower() not in DATA_SUFFIXES or any(p.startswith(".") for p in relative.parts):
            continue
        if path.suffix == ".json":
            value = portable_json(json.loads(path.read_text()))
            if relative.as_posix() == "digital_twin.json":
                value.setdefault("runtime", {}).update(offline=True, external_models_enabled=False)
            yield relative.as_posix(), json_bytes(value)
        else:
            yield relative.as_posix(), path


def code_entries(root=ROOT):
    root = Path(root)
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
    for name in sorted(set(names)):
        if not name:
            continue
        rel = Path(name)
        if name not in CODE_FILES and rel.parts[0] not in CODE_DIRS:
            continue
        if (rel.parts[:2] == ("web", "data") or any((p.startswith(".") and name != "live/.env.example") or p == "__pycache__" for p in rel.parts)
                or rel.suffix in {".pyc", ".env", ".log"} or rel.name in {"secrets.env", ".mcp_token"}):
            continue
        path = root / rel
        if path.is_symlink():
            raise ValueError(f"Code symlink is not portable: {name}")
        if path.is_file():
            yield name, path


def write_archive(destination, entries):
    inventory = []
    total = 0
    with destination.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=6) as zipped, tarfile.open(fileobj=zipped, mode="w|") as archive:
        def add(name, content, record=True):
            nonlocal total
            if isinstance(content, bytes):
                size, sha, stream = len(content), hashlib.sha256(content).hexdigest(), io.BytesIO(content)
            else:
                size, sha, stream = content.stat().st_size, digest(content), content.open("rb")
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = size, 0o644, 0
            with stream:
                archive.addfile(member, stream)
            total += size
            if record:
                inventory.append({"path": name, "bytes": size, "sha256": sha})
        for name, content in entries:
            add(name, content)
        add("asset-inventory.json", json_bytes({"schema_version": 1, "files": inventory}), record=False)
    return {"path": destination.name, "sha256": digest(destination), "bytes": destination.stat().st_size,
            "unpacked_bytes": total, "file_count": len(inventory) + 1, "inventory": "asset-inventory.json"}


def build_release(output, baseline, extraction=None, release="0.1.1", public_base="https://assets.scan2work.app", root=ROOT):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release):
        raise ValueError("Release must be a safe version identifier")
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace release directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".scan2work-release-", dir=output.parent))
    try:
        artifacts = {}
        for kind, entries in [("baseline", data_entries(baseline))] + ([("extraction", data_entries(extraction))] if extraction else []):
            print(f"Packing {kind}…", flush=True)
            artifacts[kind] = write_archive(stage / f"scan2work-{kind}.tar.gz", entries)
            twin = json.loads((Path(baseline if kind == "baseline" else extraction) / "digital_twin.json").read_text())
            artifacts[kind].update(objects=len(twin["assets"]), source_sha256=twin["scene"]["source_sha256"],
                                   splats=twin["scene"]["splat_binary"]["exported_splats"], scale_verified=False)
        print("Packing code…", flush=True)
        artifacts["code"] = write_archive(stage / "scan2work-code.tar.gz", code_entries(root))
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
        manifest = {"schema_version": 1, "release": release, "python": "3.12", "platforms": ["macOS", "Linux"],
                    "source_revision": revision, "working_tree_changes_included": dirty,
                    "artifacts": artifacts, "default_artifacts": ["code", "baseline"],
                    "description": "Prepared equipment-room demo with optional class-free extraction review. Synthetic operating data; scale unverified.",
                    "runtime": {"offline": True, "external_models_enabled": False},
                    "excluded": ["credentials", "field_log.json", "scene_edits.json", "decisions_cache.json", "virtualenvs", "model weights"]}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        shutil.copy2(root / "scripts/install_scan2work.py", stage / "install.py")
        (stage / "install.sh").write_text((root / "distribution/install.sh").read_text().replace("@RELEASE@", release).replace("https://assets.scan2work.app", public_base.rstrip("/")))
        for source, name in [("docs/installation.md", "INSTALL.md"), ("docs/dependencies.md", "DEPENDENCIES.md"), ("docs/cloudflare-r2.md", "HOSTING.md"), ("docs/public-demo.md", "PUBLIC-DEMO.md")]:
            text = (root / source).read_text()
            for old, new in [("cloudflare-r2.md", "HOSTING.md"), ("dependencies.md", "DEPENDENCIES.md"), ("public-demo.md", "PUBLIC-DEMO.md")]:
                text = text.replace(f"]({old})", f"]({new})")
            text = text.replace("[asset import guide](asset-import-guide.md)", "asset import guide (`app/docs/asset-import-guide.md`)")
            text = text.replace("[Packaging audit](packaging-audit.md)", "Packaging audit (`app/docs/packaging-audit.md`)")
            text = text.replace("[HTTPS edge](../distribution/cloudflare/README.md)", "HTTPS edge (`app/distribution/cloudflare/README.md`)")
            text = text.replace("[host setup](../distribution/host/README.md)", "host setup (`app/distribution/host/README.md`)")
            text = text.replace("[0.1.0 publication verification](release-0.1.0.md)", "0.1.0 publication verification (`app/docs/release-0.1.0.md`)")
            (stage / name).write_text(text)
        url = public_base.rstrip("/") + "/releases/" + release
        (stage / "index.html").write_text(download_page(manifest, url))
        files = sorted(p for p in stage.iterdir() if p.is_file())
        (stage / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in files))
        stage.rename(output)
        print(json.dumps({"release_dir": str(output), "artifacts": artifacts}, indent=2), flush=True)
        return manifest
    except BaseException:
        shutil.rmtree(stage)
        raise


def download_page(manifest, base):
    release = html.escape(manifest["release"])
    links = "".join(f'<li><a href="{html.escape(a["path"])}">{html.escape(k.title())}</a> — {a["bytes"] / 1e6:.1f} MB</li>' for k, a in manifest["artifacts"].items())
    command = f"curl -fsSLo install-scan2work.sh {base}/install.sh &amp;&amp; bash install-scan2work.sh --install-dir ~/scan2work-{release} --with-extraction --launch"
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Scan2Work {release} downloads</title>
<style>body{{font:18px/1.6 system-ui;max-width:860px;margin:50px auto;padding:0 24px;background:#f6f7f4;color:#173229}}a{{color:#195e48}}pre{{padding:20px;background:#e3ebe5;overflow:auto;font-size:14px}}li{{margin:8px 0}}</style>
<h1>Scan2Work</h1><p>Equipment-room demo · {release}</p><p>Explore 504 room objects, inspect the synthetic fault scenario, isolate and save objects, and review 1,933 extraction regions in a separate view.</p>
<h2>Install and open</h2><p>Requires Python 3.12 and a WebGL 2 browser on macOS or Linux. First install downloads Python packages; the prepared demo runs offline. No GPU or API key required.</p><pre>{command}</pre>
<p>Omit <code>--with-extraction</code> for the smaller room-only install. Stop with Ctrl+C; restart using <code>python3.12 ~/scan2work-{release}/start.py --open</code>. Install each new release into a new directory; published archives are immutable.</p>
<p>The local installation saves technician jobs, scene changes and new exact-source object exports. A hosted preview keeps shared state read-only and room edits browser-local. Optional model authoring needs a full repository checkout and separate model downloads.</p>
<h2>Files</h2><ul>{links}<li><a href="install.py">Python installer</a></li><li><a href="manifest.json">Release manifest</a></li><li><a href="SHA256SUMS">SHA-256 checksums</a></li><li><a href="INSTALL.md">Installation instructions</a></li><li><a href="DEPENDENCIES.md">Dependency and module map</a></li><li><a href="HOSTING.md">R2 hosting instructions</a></li><li><a href="PUBLIC-DEMO.md">Hosted preview operation</a></li></ul>
<p>Includes prepared Take apart assemblies and AI-generated room models from upstream PR #3. These models are illustrative, not measured reconstruction. This is a scan-based prototype. Operating records are synthetic, region labels need review, and physical scale is unverified. IFC export is a geometry demonstration, not a surveyed BIM.</p></html>'''


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--extraction", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--release", default="0.1.1")
    p.add_argument("--public-base", default="https://assets.scan2work.app")
    args = p.parse_args()
    build_release(args.output, args.baseline, args.extraction, args.release, args.public_base)
