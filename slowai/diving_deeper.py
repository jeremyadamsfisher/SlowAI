# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_diving_deeper.ipynb.

# %% auto 0
__all__ = ['MNIST_URL', 'data_fp', 'download_file', 'download_mnist']

# %% ../nbs/01_diving_deeper.ipynb 3
import gzip
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

import matplotlib.pyplot as plt
import requests
import torch

from .overview import show_image

# %% ../nbs/01_diving_deeper.ipynb 5
# Get the data
MNIST_URL = "https://github.com/mnielsen/neural-networks-and-deep-learning/blob/master/data/mnist.pkl.gz?raw=true"


def download_file(url, destination):
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(destination, "wb") as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)


def download_mnist(path_gz=Path("./data") / "mnist.pkl.gz"):
    path_gz.parent.mkdir(exist_ok=True, parents=True)
    download_file(MNIST_URL, path_gz)
    return path_gz


data_fp = download_mnist()
data_fp
