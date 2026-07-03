#!/usr/bin/env bash
# Start the local, no-root Ollama server used by `motodeals extract`.
# Models and the binary live on the roomy /extra partition, not ~/.
# Usage:  ./scripts/serve_ollama.sh        (leave running in a terminal)
set -euo pipefail

OLLAMA_DIR="/home/spongypower928/extra/ollama"
export OLLAMA_MODELS="$OLLAMA_DIR/models"
export OLLAMA_HOST="127.0.0.1:11434"

# On the real machine with the GTX 1660, Ollama auto-detects CUDA (the bundled
# lib/ollama/cuda_v12|v13 runners) and offloads to GPU. No config needed.
exec "$OLLAMA_DIR/bin/ollama" serve
