# Publish downloads on Cloudflare R2

Use `scan2work.app` for the product website and **`assets.scan2work.app`** for versioned downloads from R2. The browser demo runs a separate read-only Python backend behind Cloudflare Workers; R2 serves the downloadable installation and assets. See the [HTTPS edge](../distribution/cloudflare/README.md) and [host setup](../distribution/host/README.md).

## One-time setup

1. In the Cloudflare account managing `scan2work.app`, create an R2 bucket named `scan2work-downloads`.
2. In that bucket's settings, connect the custom domain `assets.scan2work.app`. Cloudflare creates the corresponding DNS configuration. Wait for the custom domain to become active. An ordinary DNS CNAME without connecting the R2 custom domain is insufficient.
3. Use an R2 API credential scoped to object read/write for this bucket. Configure it locally with `aws configure --profile scan2work-r2`, region `auto`. Keep credentials out of this repository, archives and browser code. Teammates downloading the public release need no credentials.

R2 public custom domains support caching; the `r2.dev` endpoint is intended for development. The bucket root does not automatically list files. Use the explicit versioned `index.html` link below or link it from your product website. See Cloudflare's [custom-domain instructions](https://developers.cloudflare.com/r2/buckets/public-buckets/) and [AWS CLI setup](https://developers.cloudflare.com/r2/examples/aws/aws-cli/).

## Build a release

Run from this repository with already prepared datasets. This copies/archives input data without running extraction or changing source files:

```sh
python3.12 distribution/build_release.py \
  --baseline /absolute/path/to/prepared-original-room \
  --extraction /absolute/path/to/prepared-expanded-extraction \
  --release 0.1.1 \
  --output /absolute/path/to/downloads/releases/0.1.1
```

Omit `--extraction` for a baseline-only release. The builder rejects an existing destination. Each archive has its own `asset-inventory.json` with file hashes; the release has artifact hashes/sizes in `manifest.json`, and checksums for all public files in `SHA256SUMS`. Code provenance includes the Git revision and whether working-tree changes were included. Use a committed clean tree for a final release.

The builder uses explicit data directories/file types. It excludes local field logs, scene edits, API caches, secrets, virtual environments and model weights. Binary native scan/render/membership files are retained exactly. JSON is compacted, offline runtime policy is explicit, and developer-local review links are replaced by launch-time configuration. It includes the current library examples; curate those intentionally before building subsequent releases.

## Upload

The publisher defaults to a **dry run**, verifies local checksums first, and prints the upload commands:

```sh
python3.12 distribution/publish_r2.py /absolute/path/to/downloads/releases/0.1.1 \
  --account-id YOUR_32_CHARACTER_CLOUDFLARE_ACCOUNT_ID
```

When that list matches the release you want to publish, add `--execute`. It uses your existing `scan2work-r2` AWS profile, uploads to `releases/0.1.1/`, and uploads the manifest last. It rejects any existing objects under that version prefix; a failed partial upload requires a fresh version. No bucket creation, DNS change, public-access change, deletion or overwrite occurs in this script.

The AWS CLI handles multipart transfer for large archives. This is more suitable for the scan/video archives than manually issuing a single upload request. Cloudflare documents the [upload methods and size limits](https://developers.cloudflare.com/r2/objects/upload-objects/).

Expected public objects:

```text
https://assets.scan2work.app/releases/0.1.1/
  index.html
  install.sh
  install.py
  manifest.json
  SHA256SUMS
  INSTALL.md
  DEPENDENCIES.md
  HOSTING.md
  PUBLIC-DEMO.md
  scan2work-code.tar.gz
  scan2work-baseline.tar.gz
  scan2work-extraction.tar.gz
```

Set immutable cache headers for these versioned objects (the uploader does so). Never replace files in an already published release. When updating, build a new version and change your website's download link after verifying it. Avoid a cacheable mutable `latest/manifest.json` pointing at mismatched files.

## Verify the hosted release

Open the explicit `index.html` URL, download `manifest.json`, and install into a fresh directory using the published command. Run `start.py --doctor`, open the room and confirm photos/video and the optional candidate-review link. Keep the working old version until this passes.

Downloads happen through the Python installer, so no browser CORS rule is needed for this package. If a future hosted viewer fetches raw R2 assets across origins, configure a separate restricted CORS policy for its exact origin and required GET/HEAD/range requests.

The [0.1.0 publication verification](release-0.1.0.md) records the live endpoints and installation checks. Generating this upload directory does **not** mean the bucket, domain or public downloads have been created.
