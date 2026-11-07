#!/usr/bin/env python3
"""Validate and upload one immutable release through an existing AWS CLI R2 profile.

Dry run by default. Does not create buckets, alter DNS or enable public access.
Credentials stay in the AWS CLI configuration/environment, never in this package.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def validate_release(folder):
    folder = Path(folder).resolve()
    names = []
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        sha, name = line.split("  ", 1)
        if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("Invalid checksum entry")
        p = folder / name
        if p.is_symlink() or not p.is_file():
            raise ValueError(f"Missing or linked release file: {name}")
        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != sha:
            raise ValueError(f"Checksum mismatch: {name}")
        names.append(name)
    required = {"manifest.json", "install.py", "install.sh"}
    if not required.issubset(names):
        raise ValueError("Release lacks its manifest/installers")
    manifest = json.loads((folder / "manifest.json").read_text())
    for artifact in manifest["artifacts"].values():
        p = folder / artifact["path"]
        if p.name != artifact["path"] or p.name not in names or p.stat().st_size != artifact["bytes"]:
            raise ValueError("Manifest/archive mismatch")
        if not any(line == f'{artifact["sha256"]}  {p.name}' for line in (folder / "SHA256SUMS").read_text().splitlines()):
            raise ValueError("Manifest checksum mismatch")
    return manifest, sorted(set(names + ["SHA256SUMS"]), key=lambda n: (n == "manifest.json", n))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("release_dir", type=Path)
    ap.add_argument("--account-id", required=True)
    ap.add_argument("--bucket", default="scan2work-downloads")
    ap.add_argument("--profile", default="scan2work-r2")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.account_id) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]+", args.bucket):
        ap.error("Expected a 32-character Cloudflare account ID and an R2 bucket name")
    manifest, names = validate_release(args.release_dir)
    version = manifest["release"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Invalid release name")
    prefix = f"releases/{version}"
    cli = ["aws", "--profile", args.profile, "--region", "auto", "--endpoint-url", f"https://{args.account_id}.r2.cloudflarestorage.com"]
    if args.execute:
        # Even a partially uploaded release must be retried under a fresh version.
        result = subprocess.run(cli + ["s3api", "list-objects-v2", "--bucket", args.bucket, "--prefix", prefix + "/", "--max-keys", "1", "--output", "json"], capture_output=True, text=True, check=True)
        if json.loads(result.stdout).get("KeyCount", 0):
            raise RuntimeError(f"Remote release prefix already exists: {prefix}. Use a new version; no overwrite was attempted.")
    for name in names:
        p = args.release_dir.resolve() / name
        content_type = {".json": "application/json", ".html": "text/html; charset=utf-8", ".gz": "application/gzip"}.get(p.suffix, "text/plain; charset=utf-8")
        cmd = cli + ["s3", "cp", str(p), f"s3://{args.bucket}/{prefix}/{name}", "--only-show-errors", "--content-type", content_type, "--cache-control", "public,max-age=31536000,immutable"]
        print(shlex.join(cmd), flush=True)
        if args.execute:
            subprocess.run(cmd, check=True)
    if not args.execute:
        print("Dry run only. Add --execute to upload these verified files. DNS and bucket access are unchanged.")


if __name__ == "__main__":
    main()
