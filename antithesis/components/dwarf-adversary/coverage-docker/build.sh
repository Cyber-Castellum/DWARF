#!/bin/bash
# Build the DWARF Haskell coverage-fuzzer image. Run from the dwarf-adversary
# package root (build context). JOBS low by default to bound peak RAM.
set -euo pipefail
TAG="${TAG:-dwarf-haskell-cov:0.1}"
JOBS="${JOBS:-1}"
docker build --platform=linux/amd64 -f coverage-docker/Dockerfile \
  --build-arg JOBS="$JOBS" -t "$TAG" .
echo "built $TAG"
