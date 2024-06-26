# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/08_activations.ipynb.

# %% auto 0
__all__ = ['set_seed', 'Conv2dWithReLU', 'CNN', 'Hook', 'HooksCallback', 'StoreModuleStats', 'StoreModuleStatsCB']

# %% ../nbs/08_activations.ipynb 3
import random
from functools import partial

import fastcore.all as fc
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn
from torchmetrics.classification import MulticlassAccuracy

from slowai.learner import (
    Callback,
    DeviceCB,
    MetricsCB,
    ProgressCB,
    TrainLearner,
    fashion_mnist,
    to_cpu,
)
from .utils import get_grid, show_image

# %% ../nbs/08_activations.ipynb 4
def set_seed(seed, deterministic=False):
    torch.use_deterministic_algorithms(deterministic)
    torch.manual_seed(seed)
    random.seed(seed)
    np.random.seed(seed)

# %% ../nbs/08_activations.ipynb 7
class Conv2dWithReLU(nn.Module):
    """Convolutional neural network with a built in activation"""

    @fc.delegates(nn.Conv2d)
    def __init__(
        self,
        *args,
        nonlinearity=F.relu,
        **kwargs,
    ):
        super().__init__()
        self.conv = nn.Conv2d(*args, **kwargs)
        self.nonlinearity = nonlinearity

    def forward(self, x):
        return self.nonlinearity(self.conv(x))

# %% ../nbs/08_activations.ipynb 8
class CNN(nn.Module):
    """Six layer convolutional neural network"""

    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(
            Conv2dWithReLU(1, 8, kernel_size=5, stride=2, padding=2),  # 14x14
            Conv2dWithReLU(8, 16, kernel_size=3, stride=2, padding=1),  # 7x7
            Conv2dWithReLU(16, 32, kernel_size=3, stride=2, padding=1),  # 4x4
            Conv2dWithReLU(32, 64, kernel_size=3, stride=2, padding=1),  # 2x2
            nn.Conv2d(64, 10, kernel_size=3, stride=2, padding=1),  # 1x1
        )

    def forward(self, x):
        return rearrange(self.layers(x), "bs c w h -> bs (c w h)")

# %% ../nbs/08_activations.ipynb 12
class Hook:
    """Wrapper for a PyTorch hook, facilitating adding instance state"""

    def __init__(self, m, f):
        self.hook = m.register_forward_hook(partial(f, self))

    def remove(self):
        self.hook.remove()

    def __del__(self):
        self.remove()

# %% ../nbs/08_activations.ipynb 13
class HooksCallback(Callback):
    """Container for hooks with clean up and and options to target certain modules"""

    def __init__(
        self,
        hook_cls,
        mods=None,
        mod_filter=fc.noop,
        on_train=True,
        on_valid=False,
    ):
        fc.store_attr()

    def before_fit(self, learn):
        if self.mods:
            mods = self.mods
        else:
            mods = fc.filter_ex(learn.model.modules(), self.mod_filter)
        self.hooks = [self.hook_cls(m) for m in mods]

    def cleanup_fit(self, learn):
        for h in self.hooks:
            h.remove()

    def __iter__(self):
        return iter(self.hooks)

    def __len__(self):
        return len(self.hooks)

# %% ../nbs/08_activations.ipynb 15
class StoreModuleStats(Hook):
    """A hook for storing the activation statistics"""

    def __init__(self, m, on_train=True, on_valid=False, periodicity=1):
        self.moments = []
        self.hists = []

        def append_moments(module, _, activations):
            if len(self.moments) % periodicity == 0:
                trn = on_train and module.training
                vld = on_valid and not module.training
                if trn or vld:
                    a = to_cpu(activations)
                    self.moments.append((a.mean(), a.std()))
                    self.hists.append(a.abs().histc(40, 0, 10))

        self.hook = m.register_forward_hook(append_moments)

    def plot(self, ax0, ax1, label):
        means, stds = zip(*self.moments)
        ax0.plot(means, label=label)
        ax1.plot(stds)

# %% ../nbs/08_activations.ipynb 16
class StoreModuleStatsCB(HooksCallback):
    """Callback for plotting the layer-wise activation statistics"""

    def __init__(
        self,
        mods=None,
        mod_filter=fc.noop,
        on_train=True,
        on_valid=False,
        hook_kwargs=None,
    ):
        fc.store_attr()
        if hook_kwargs:
            self.hook_cls = partial(StoreModuleStats, **hook_kwargs)
        else:
            self.hook_cls = StoreModuleStats

    def hist_plot(self):
        fig, axes = get_grid(len(self.hooks))
        for ax, h in zip(axes.flatten(), self.hooks):
            hist = torch.stack(h.hists).T.float().log1p()
            show_image(hist, ax, origin="lower")
        fig.tight_layout()

    def mean_std_plot(self):
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        ax0, ax1 = axes
        ax0.set(title="Mean")
        ax1.set(title="STD")
        for i, h in enumerate(self.hooks):
            h.plot(*axes, label=f"layer {i}")
        fig.legend()
        fig.tight_layout()
