# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/07_autoencoders.ipynb.

# %% auto 0
__all__ = ['deconv', 'get_model']

# %% ../nbs/07_autoencoders.ipynb 3
import torch
import torch.nn.functional as F
from torch import nn, optim
from tqdm import tqdm

from .convs import conv, def_device, fashion_mnist, to_device
from .datasets import show_images

# %% ../nbs/07_autoencoders.ipynb 5
def deconv(c_in, c_out, ks=3, act=True):
    layers = [
        nn.UpsamplingNearest2d(scale_factor=2),
        nn.Conv2d(c_in, c_out, stride=1, kernel_size=ks, padding=ks // 2),
    ]
    if act:
        layers.append(nn.ReLU())
    return nn.Sequential(*layers)

# %% ../nbs/07_autoencoders.ipynb 8
def get_model():
    # input.shape[:2] == 28x28
    return nn.Sequential(
        nn.ZeroPad2d(padding=2),  # 32x32
        conv(1, 2),  # 16x16
        conv(2, 4),  # 8x8
        conv(4, 8),  # 4x4
        deconv(8, 4),  # 8x8
        deconv(4, 2),  # 16x16
        deconv(2, 1, act=False),  # 32x32
        nn.ZeroPad2d(padding=-2),  # 28x28
        nn.Sigmoid(),
    ).to(def_device)
