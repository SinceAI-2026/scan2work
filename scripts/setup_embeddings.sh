#!/bin/bash
# One-shot EmbeddingGemma setup. Run from anywhere:  bash ~/Valmet_hack/valmet-twin/scripts/setup_embeddings.sh
set -e
cd "$(dirname "$0")/.."
if ! grep -qE '^HF_TOKEN=.+' secrets.env 2>/dev/null; then
  echo "HF_TOKEN is empty in secrets.env."
  echo "1) Accept the licence: https://huggingface.co/google/embeddinggemma-300m"
  echo "2) Create a read token: https://huggingface.co/settings/tokens"
  echo "3) Paste it after HF_TOKEN= in secrets.env, save, and re-run this script."
  exit 1
fi
PKG="sentence-transformers>=5"
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python .venv/bin/python "$PKG"
elif [ -x "$HOME/.local/bin/uv" ]; then
  "$HOME/.local/bin/uv" pip install -q --python .venv/bin/python "$PKG"
else
  ./.venv/bin/python -m ensurepip --upgrade >/dev/null
  ./.venv/bin/python -m pip install -q "$PKG"
fi
./.venv/bin/python -m pipeline.run | grep -E "retrieval|decisions|Done|target"
grep -q '"backend": "embeddinggemma"' web/data/rag_index.json && echo "OK: EmbeddingGemma is active." || echo "Still on TF-IDF - send Claude the [retrieval] line above."
