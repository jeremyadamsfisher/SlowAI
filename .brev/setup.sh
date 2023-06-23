#!/bin/bash

set -eo pipefail

pip install -q jupyterlab nbdev

if nvidia-smi | grep -q "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."; then
  echo "NVIDIA GPU found! Installing GPU-accelerated PyTorch..."
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
else
  echo "No NVIDIA GPU found! Installing CPU-only PyTorch..."
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi

nohup jupyter-lab &
nohup nbdev_preview &