# Install Scan2Work

Release **0.1.1** installs the restored Wael room, reusable asset/export modules, and PR #3’s prepared Take apart / AI 3D assets. Installation does not run detection, train a model, or call a paid API. The viewer is branded **Scan2Work**. AI-generated geometry is illustrative and physical scale remains unverified.

## Quick start

You need **Python 3.12**, a browser with WebGL 2, and internet access for the first dependency download. The package targets macOS and Linux. Use at least 8 GB of free disk space for the full installation and temporary extraction. The release manifest gives exact archive and unpacked sizes. No NVIDIA GPU, Docker, Node.js, ffmpeg, account, or API key is required to run this prepared demo.

On macOS with Homebrew, install Python using `brew install python@3.12`. On Ubuntu 24.04, install `python3.12` and `python3.12-venv` using the system package manager. The installer never runs sudo or modifies system Python. The pinned numerical wheels on Apple Silicon require macOS 14 or newer. Windows is not a validated target; use Linux/WSL2 with a WebGL-capable host browser.

Install both views from the versioned release and open the room in one command:

```sh
curl -fsSLo install-scan2work.sh https://assets.scan2work.app/releases/0.1.1/install.sh && bash install-scan2work.sh --install-dir ~/scan2work-0.1.1 --with-extraction --launch
```

You can inspect the downloaded script before running it. Maintainers can publish future releases using the [R2 guide](cloudflare-r2.md).

To install now from a downloaded/copied release directory, run:

```sh
python3.12 install.py --manifest manifest.json --install-dir ~/scan2work-0.1.1 --with-extraction --launch
```

Omit `--with-extraction` for the smaller room-only installation. The installer verifies archive SHA-256 and byte counts, creates a private Python environment, installs the exact package versions, and checks the server before completing. It refuses an existing destination so a reinstall cannot replace your saved work.

Stop the servers with Ctrl+C. Restart later:

```sh
python3.12 ~/scan2work-0.1.1/start.py --open
```

The default room is at `http://127.0.0.1:8766/`. If installed, complete extraction review is at `http://127.0.0.1:8768/`. Both run on your computer. If those ports are occupied:

```sh
python3.12 ~/scan2work-0.1.1/start.py --port 8770 --extraction-port 8772 --open
```

The candidate link follows the selected review port. With no optional extraction dataset installed, that link is absent; the baseline's candidate overlay and source evidence remain usable.

## Browser preview or local installation

The browser preview at `https://scan2work.app/` uses prepared synthetic data.
The main room enables shared inspection jobs and the optional voice assistant;
visitor checks and notes are visible to other visitors. The separate extraction
review remains read-only with voice disabled. Scene movement, cloning and removal
stay in each browser. The hosted preview does not allow shared library exports.
The immutable 0.1.1 installer has its own earlier code snapshot and keeps voice
off by default. Use this repository with its prepared data to try newer features.

Install locally to save jobs and scene changes or export additional objects from
their exact native source rows. Local operation does not require a server
account. Legacy photo refinement, new GLB reconstruction, training and voice
services remain separate optional development workflows; the prepared installer
does not activate them. See [hosted-mode operation](public-demo.md) and the
[dependency map](dependencies.md) for the boundary.

## What is included

| Component | Contents |
| --- | --- |
| Code | Python backend, local Three.js viewer, reusable modules, tests, installation tools and documentation |
| Default room | 504 original object groupings, preserved floor/navigation, 30 synthetic workbook bindings, source photos, recorded AR walkthrough, five saved example objects, exact native Gaussian membership and source PLY |
| Illustrative 3D assets | Five split assemblies and 18 AI-generated GLBs; models display only where they can be matched to the selected dataset; model dimensions are unverified |
| Candidate overlay | 1,933 unreviewed regions projected onto the original room, correspondence links and evidence crops; separate from original object ownership |
| Optional extraction | Complete 1,933-region segmentation/rendering and exact source membership in its own dataset and server |

The original room and expanded extraction are different segmentations. They are kept separate. The package does not turn candidate counts into an accuracy claim, reconstruct missing surfaces, or calibrate physical scale. IFC export remains a schematic geometry demonstration. New exact-source library exports preserve observed Gaussians; they do not synthesize a complete textured object.

All packaged operating values are synthetic workbook data. Local technician notes, scene edits, credentials, model weights and API caches are excluded. The distribution does not bundle the experimental H100/VLSplat training environments, `local_models/` authoring directory or original raw walkthrough MOV files; the prepared video, frames and image evidence are included. The optional local SAM/SigLIP/DINO/OWL segmentation needs a full source checkout, its separate dependencies and initial model downloads. Optional training/service integrations are mapped in [dependencies](dependencies.md).

## Installed layout and team reuse

```text
scan2work-0.1.1/
  start.py                     # launch, port selection and doctor
  .venv/                       # private pinned Python environment
  app/                         # reusable source code and tests
  data/baseline/                # original room, native source, photos and review overlay
  data/extraction/              # optional expanded candidates
  install.json                 # exact release and selected artifacts
```

`app/web` needs no frontend build. Modules can be imported independently; see the [module/dependency map](dependencies.md). The source row map, membership and native PLY must travel together. A filename or object index alone is insufficient to align a different scan.

New saved objects live under each dataset's `library/`. Job notes are written to `field_log.json`; reversible changes are written to `scene_edits.json`. Back up your whole `data/` directory before moving to a newer release. Browser preferences are separate in local storage.

## Verify, update, remove

```sh
python3.12 ~/scan2work-0.1.1/start.py --doctor
```

Doctor checks the prepared files, native source alignment metadata, Python imports and server responses without calling external models. A successful install does not measure recognition quality or real-world collision safety.

Install a new version into a **new directory**, then verify it before migrating notes or library objects. Keep the old installation until you have checked object IDs and source hashes. There is intentionally no automatic merge of two datasets. To remove an installation, stop its servers and delete that installation directory after saving any work you want to keep. Do not move an existing `.venv`; reinstall at the new location instead.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Download URL not found | Check the version and hostname, then retry. Use an intact copied release folder if the network is unavailable. |
| Python version rejected | Run with Python 3.12. An older macOS `python3` may be Apple's 3.9 interpreter. |
| `venv`/`ensurepip` unavailable | Install the matching Python 3.12 venv component, then retry. |
| No compatible package wheel | Check OS, architecture and Python version. The installer uses binary wheels; it does not silently compile numerical libraries. |
| Checksum/download failure | Retry from an intact release directory or mirror. No existing installation is replaced. |
| Existing destination | Restart it with `start.py`, or install a newer release into another directory. |
| Port already in use | Pick unused `--port` and `--extraction-port` values. |
| Empty or slow 3D viewport | Enable browser hardware acceleration/WebGL 2 and reduce quality using Display. This is independent of CUDA. |
| Voice unavailable | Expected in this offline prepared-data release. Use the workbook-grounded on-screen guidance; see the original README for separately configured voice services. |

The first install still needs access to the Python package index unless packages are already cached. This release is not an air-gapped Python-wheel bundle. After installation, the offline viewer, photos, video, routes, exact exports and cached workbook guidance do not require network access.
