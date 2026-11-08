# Contributing to Scan2Work

Scan2Work was built for Valmet Automation’s Since AI 2026 challenge. Start with
[README.md](README.md), the [module map](docs/dependencies.md), and [AGENTS.md](AGENTS.md).

## Run and check a change

Use Python 3.12 and the prepared-data instructions in the README. The viewer is
plain JavaScript with locally bundled Three.js; it has no npm build step. Node.js
is needed only for the JavaScript tests. Keep datasets, local job logs and credentials
outside commits. Use a disposable copy of prepared data for tests that write jobs.

Run the relevant checks from the repository root:

```sh
node --test web/*.test.mjs
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Some Python and scene-specific tests require prepared data or optional packages;
report any skipped or unavailable checks in your pull request. Test viewer changes
in a WebGL 2 browser at desktop and narrow screen widths. For changes to `web/app.js`,
run the two idempotent patch checks specified in AGENTS.md.

Do not run `python -m pipeline.run` or `scripts/check.sh` as a routine test. They
rebuild data and may call paid model APIs. Review the [pipeline guide](docs/pipeline.md)
and explicitly choose a separate output directory before rebuilding.

## Pull requests

Explain the user-visible change, why it is needed, and how you checked it. Keep
workbook data distinguishable from inferred geometry, preserve source evidence,
and do not invent device readings or physical safety guarantees. Use synthetic
examples when reporting issues. Never post credentials, private scans or job notes.

Read the licensing and attribution section of the README before reusing or
redistributing code or data. A public repository does not grant a new license to
challenge materials or third-party components.
