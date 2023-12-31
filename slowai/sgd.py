# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/10_stable_sgd.ipynb.

# %% auto 0
__all__ = ['train', 'BaseSchedulerCB', 'BatchSchedulerCB', 'RecorderCB', 'train_1cycle']

# %% ../nbs/10_stable_sgd.ipynb 3
from functools import partial

import fastcore.all as fc
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
from torch.optim import lr_scheduler
from torchmetrics.classification import MulticlassAccuracy

from .activations import StoreModuleStatsCB, set_seed
from slowai.initializations import (
    CNNWithGeneralReLUAndBatchNorm,
    GeneralReLU,
    init_leaky_weights,
    set_seed,
)
from slowai.learner import (
    Callback,
    DeviceCB,
    MetricsCB,
    MomentumCB,
    ProgressCB,
    TrainLearner,
    fashion_mnist,
)
from .utils import glomf as g

# %% ../nbs/10_stable_sgd.ipynb 5
def train(model, lr, n_epochs=3, bs=512, opt_func=torch.optim.SGD, cbs=tuple()):
    """Train a Fashion MNIST model"""
    cbs_ = [
        MetricsCB(MulticlassAccuracy(num_classes=10)),
        DeviceCB(),
        ProgressCB(plot=True),
    ]
    if cbs:
        cbs_.extend(cbs)
    TrainLearner(
        model,
        fashion_mnist(bs),
        F.cross_entropy,
        lr=lr,
        cbs=cbs_,
        opt_func=opt_func,
    ).fit(n_epochs)

# %% ../nbs/10_stable_sgd.ipynb 38
class BaseSchedulerCB(Callback):
    """Base callback class for schedulers"""

    def __init__(self, scheduler_f, **kwargs):
        self.scheduler_f = scheduler_f
        self.sched_kwargs = kwargs
        self.sched = None

    def before_fit(self, learn):
        self.sched = self.scheduler_f(learn.opt, **self.sched_kwargs)

    def _step(self, learn):
        if learn.training:
            self.sched.step()

# %% ../nbs/10_stable_sgd.ipynb 39
class BatchSchedulerCB(BaseSchedulerCB):
    """Step the scheduler every batch"""

    def after_batch(self, learn):
        self._step(learn)

# %% ../nbs/10_stable_sgd.ipynb 40
class RecorderCB(Callback):
    """Record internal state values at each batch."""

    def __init__(self, **d):
        self.d = d
        self.learn = None

    def before_fit(self, learn):
        self.learn = learn
        self.recs = {k: [] for k in self.d}
        self.pg = learn.opt.param_groups[0]

    def after_batch(self, learn):
        if not learn.training:
            return
        for k, v in self.d.items():
            self.recs[k].append(v(self))

    def plot(self, **kwargs):
        n = len(self.recs)
        if "figsize" not in kwargs:
            K = 3
            kwargs["figsize"] = (K * n, K)
        fig, axes = plt.subplots(1, n, **kwargs)
        if n > 1:
            axes = axes.flatten()
        else:
            axes = [axes]
        for ax, (k, v) in zip(axes, self.recs.items()):
            ax.plot(v, label=k)
            ax.legend()
        fig.tight_layout()

# %% ../nbs/10_stable_sgd.ipynb 47
def train_1cycle(model, lr=1e-2, n_epochs=3, extra_cbs=[]):
    dls = fashion_mnist(512)
    T_max = len(dls["train"]) * n_epochs
    scheduler = BatchSchedulerCB(lr_scheduler.OneCycleLR, max_lr=lr, total_steps=T_max)
    recorder = RecorderCB(lr=g("pg.lr"), mom=g("pg.betas.0"))
    cbs = [scheduler, recorder, stats, *extra_cbs]
    try:
        stats = StoreModuleStatsCB(mods=model.layers)
    except AttributeError:
        stats = None
    else:
        cbs.append(stats)
    train(
        model,
        lr,
        n_epochs,
        opt_func=torch.optim.AdamW,
        cbs=cbs,
    )
    return recorder, stats
