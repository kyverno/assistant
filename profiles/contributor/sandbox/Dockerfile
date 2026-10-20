FROM nvcr.io/nvidia/base/ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq less make python3 ripgrep unzip \
    && rm -rf /var/lib/apt/lists/*
