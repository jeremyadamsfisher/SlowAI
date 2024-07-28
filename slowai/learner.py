# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/07_learner.ipynb.

# %% auto 0
__all__ = ['pipe', 'to_tensor', 'DataLoaders', 'batchify', 'tensorize_images', 'CancelFitException', 'CancelBatchException',
           'CancelEpochException', 'Callback', 'with_cbs', 'only', 'Learner', 'TrainCB', 'MetricsCB', 'DeviceCB',
           'after', 'before', 'ProgressCB', 'to_cpu', 'fashion_mnist', 'TrainLearner', 'MomentumCB', 'LRFinderCB',
           'lr_find']

# %% ../nbs/07_learner.ipynb 3
import math
import multiprocessing
import tempfile
from copy import copy, deepcopy
from functools import lru_cache, partial
from pathlib import Path
from typing import Mapping, Sequence, Type, Union

import fastcore.all as fc
import matplotlib.pyplot as plt
import torch
import torch.nn.functional as F
import torchmetrics
import torchvision.transforms as T
from datasets import load_dataset, load_from_disk
from fastprogress import master_bar, progress_bar
from IPython.utils import io
from torch import optim
from torch.optim.lr_scheduler import ExponentialLR
from torch.utils.data import DataLoader, default_collate

from .autoencoders import get_model as get_ae_model
from .convs import def_device, fit, to_device
from .utils import Suppressor, show_images

# %% ../nbs/07_learner.ipynb 6
class DataLoaders:
    """Wrapper around huggingface datasets to facilitate raw pytorch work"""

    def __init__(
        self,
        splits,
        nworkers: int = multiprocessing.cpu_count() // 2,
        bs=32,
        collate_fn=default_collate,
        tdir=tempfile.TemporaryDirectory().name,
    ):
        self.splits = splits
        self.nworkers = nworkers
        self.bs = bs
        self.collate_fn = collate_fn
        self.tdir = tdir

    @classmethod
    def from_dsd(cls, dsd, **kwargs):
        return cls(splits=dsd, **kwargs)

    @classmethod
    def from_hf(cls, dataset_id, **kwargs):
        dsd = load_dataset(dataset_id)
        return cls.from_dsd(dsd, **kwargs)

    def with_transforms(self, ts, batched=True, lazy=False, splits=None):
        def map_(batch):
            for feature, transform in ts.items():
                batch[feature] = transform(batch[feature])
            return batch

        # TODO: use a function here
        if splits is None:
            if lazy:
                assert batched, "Lazy transforms must be batched"
                # TODO: make this accretive
                self.splits.set_transform(map_)
            else:
                self.splits = self.splits.map(map_, batched=batched)
        else:
            for split in splits:
                if lazy:
                    assert batched, "Lazy transforms must be batched"
                    # TODO: make this accretive
                    self.splits[split].set_transform(map_)
                else:
                    self.splits[split] = self.splits[split].map(map_, batched=batched)

        return self

    def listify(self, columns=None):
        """Yield a list instead of a dictionary"""
        if columns is None:
            columns = self.splits["train"].features

        s = copy(self)

        def collate_fn(examples):
            cols = default_collate(examples)
            return [cols[f] for f in columns]

        s.collate_fn = collate_fn
        return s

    def get_unique_outputs(self, column):
        outputs = set()
        for _, split in self.splits.items():
            for row in split:
                output = row[column]
                if isinstance(output, torch.Tensor):
                    output = output.item()
                outputs.add(output)
        return sorted(outputs)

    def dl(self, split, nworkers=None):
        ds = self.splits[split]
        nworkers = self.nworkers if nworkers is None else nworkers
        if nworkers > 0:
            ds_format = copy(ds.format)
            dsd = ds.with_format(
                "torch"
            )  # Doesn't matter which format, but needs to be serializable
            dir_ = Path(self.tdir) / split
            if not dir_.exists():
                if fc.IN_JUPYTER:
                    with io.capture_output():
                        dsd.save_to_disk(dir_)
                else:
                    dsd.save_to_disk(dir_)
            ds = load_from_disk(dir_).with_format(**ds_format)
        return DataLoader(
            ds,
            batch_size=self.bs,
            collate_fn=self.collate_fn,
            num_workers=nworkers,
        )

    def peek(self, split="train"):
        dl = self.dl(split, nworkers=0)
        batch = next(iter(dl))
        return batch

    @lru_cache
    def __getitem__(self, split):
        return self.dl(split)

# %% ../nbs/07_learner.ipynb 10
pipe = [T.PILToTensor(), T.ConvertImageDtype(torch.float)]
to_tensor = T.Compose(pipe)


def batchify(f):
    """Convert a function that processes a single feature
    to processing a list of features"""

    def inner_(batch):
        return [f(example) for example in batch]

    return inner_


def tensorize_images(dls, feature="image", normalize=True, pipe=pipe):
    """Tensorize and normalize the image feature"""
    if normalize:
        # Sample 100 images to estimate the mean and standard deviation
        imgs = dls.splits["train"].shuffle()[:100][feature]
        pixels = torch.stack([to_tensor(img) for img in imgs]).view(-1)
        mu = pixels.mean()
        sigma = pixels.std()
        to_norm_tensor = T.Compose([*pipe, T.Normalize([mu], [sigma])])

        return dls.with_transforms({feature: batchify(to_norm_tensor)}, lazy=True)
    else:
        return dls.with_transforms({feature: batchify(T.Compose(pipe))}, lazy=True)

# %% ../nbs/07_learner.ipynb 20
class CancelFitException(Exception):
    """Exit fit context"""


class CancelBatchException(Exception):
    """Skip to the next batch"""


class CancelEpochException(Exception):
    """Skip to the next epoch"""

# %% ../nbs/07_learner.ipynb 22
class Callback:
    """Modify the training behavior"""

    def __init_subclass__(cls, order=0) -> None:
        cls.order = order
        super().__init_subclass__()

# %% ../nbs/07_learner.ipynb 23
class with_cbs:
    """Run the callbacks lifecycle at the apropriate time"""

    def __init__(self, nm):
        self.nm = nm

    def __call__(self, f):
        def _f(o, *args, **kwargs):
            try:
                o.callback(f"before_{self.nm}")
                f(o, *args, **kwargs)
                o.callback(f"after_{self.nm}")
            except globals()[f"Cancel{self.nm.title()}Exception"]:
                pass
            finally:
                o.callback(f"cleanup_{self.nm}")

        return _f

# %% ../nbs/07_learner.ipynb 24
def only(f):
    """If the lifecycle hook is decorated as such, only run this
    hook and not other callbacks' hooks"""
    f.only = True
    return f

# %% ../nbs/07_learner.ipynb 25
class Learner:
    """Flexible training loop"""

    def __init__(
        self,
        model,
        dls,
        loss_func=F.mse_loss,
        lr=0.1,
        cbs=None,
        opt_func=optim.SGD,
    ):
        cbs = fc.L(cbs)
        fc.store_attr()

    def run_cbs(self, method_nm):
        for cb in self.cbs:
            method = getattr(cb, method_nm, None)
            if method is not None:
                if getattr(method, "only", False):
                    method(self)
                    return
        for cb in sorted(self.cbs, key=lambda cb: cb.order):
            method = getattr(cb, method_nm, None)
            if method is not None:
                method(self)

    @with_cbs("batch")
    def _one_batch(self):
        self.predict()
        self.callback("after_predict")
        self.get_loss()
        self.callback("after_loss")
        if self.training:
            self.backward()
            self.callback("after_backward")
            self.step()
            self.callback("after_step")
            self.zero_grad()

    @with_cbs("epoch")
    def _one_epoch(self):
        for self.iter, self.batch in enumerate(self.dl):
            self._one_batch()

    def one_epoch(self, training):
        self.model.train(training)
        # Note that __getattr__ is lru_cache'd
        # TODO: test whether this actually makes things faster
        self.dl = self.dls["train" if training else "test"]
        self._one_epoch()

    @with_cbs("fit")
    def _fit(self, train, valid):
        for self.epoch in self.epochs:
            if train:
                self.one_epoch(True)
            if valid:
                torch.no_grad()(self.one_epoch)(False)

    def fit(self, n_epochs=1, train=True, valid=True, cbs=None, lr=None):
        with tempfile.TemporaryDirectory() as tdir:
            self.dls.tdir = tdir
            cbs = fc.L(cbs)
            # `add_cb` and `rm_cb` were added in lesson 18
            for cb in cbs:
                self.cbs.append(cb)
            try:
                self.n_epochs = n_epochs
                self.epochs = range(n_epochs)
                if lr is None:
                    lr = self.lr
                if self.opt_func:
                    self.opt = self.opt_func(self.model.parameters(), lr)
                self._fit(train, valid)
            finally:
                for cb in cbs:
                    self.cbs.remove(cb)

    def __getattr__(self, name):
        if name in ("predict", "get_loss", "backward", "step", "zero_grad"):
            return partial(self.callback, name)
        raise AttributeError(name)

    def callback(self, method_nm):
        self.run_cbs(method_nm)

    @property
    def training(self):
        return self.model.training

# %% ../nbs/07_learner.ipynb 27
class TrainCB(Callback):
    """Training specific behaviors for the `Learner`"""

    def predict(self, learn):
        xb = learn.batch
        learn.preds = learn.model(*xb)

    def get_loss(self, learn):
        _, yb = learn.batch
        learn.loss = learn.loss_func(learn.preds, yb)

    def backward(self, learn):
        learn.loss.backward()

    def step(self, learn):
        learn.opt.step()

    def zero_grad(self, learn):
        learn.opt.zero_grad()

# %% ../nbs/07_learner.ipynb 29
class MetricsCB(Callback):
    """Update and print metrics"""

    def __init__(self, *ms, **metrics):
        for o in ms:
            metrics[type(o).__name__] = o
        self.metrics = metrics
        self.all_metrics = copy(metrics)
        self.all_metrics["loss"] = self.loss = torchmetrics.aggregation.MeanMetric()

    def _log(self, d, learn):
        print(d)

    def before_fit(self, learn):
        learn.metrics = self

    def before_epoch(self, learn):
        [o.reset() for o in self.all_metrics.values()]

    def after_epoch(self, learn):
        log = {k: f"{v.compute():.3f}" for k, v in self.all_metrics.items()}
        log["epoch"] = learn.epoch
        log["train"] = "train" if learn.model.training else "eval"
        self._log(log, learn)

    def after_batch(self, learn):
        x, y = to_cpu(learn.batch)
        for m in self.metrics.values():
            m.update(learn.preds.cpu(), y)
        self.loss.update(learn.loss.cpu(), weight=len(x))

# %% ../nbs/07_learner.ipynb 33
class DeviceCB(Callback):
    """Move tensors and model to the CPU/GPU/etc"""

    def __init__(self, device=def_device):
        fc.store_attr()

    def before_fit(self, learn):
        if hasattr(learn.model, "to"):
            learn.model.to(self.device)

    def before_batch(self, learn):
        learn.batch = to_device(learn.batch, device=self.device)


def after(callback_cls: Union[Sequence[Type[Callback]], Type[Callback]]):
    """Run a callback after another callback"""
    if isinstance(callback_cls, type):
        return callback_cls.order + 1
    else:
        return max(c.order for c in callback_cls) + 1


def before(callback_cls: Union[Sequence[Type[Callback]], Type[Callback]]):
    """Run a callback before another callback"""
    if isinstance(callback_cls, type):
        return callback_cls.order - 1
    else:
        return min(c.order for c in callback_cls) + 1


class ProgressCB(Callback, order=after(MetricsCB)):
    """Report the progress"""

    def __init__(self, plot=False, periodicity=10):
        self.plot = plot
        self.periodicity = periodicity
        self.i = 0

    def before_fit(self, learn):
        learn.epochs = self.mbar = master_bar(learn.epochs)
        self.first = True
        if hasattr(learn, "metrics"):
            learn.metrics._log = self._log
        self.losses = []
        self.val_losses = []

    def _log(self, d, learn):
        if self.first:
            self.mbar.write(list(d), table=True)
            self.first = False
        self.mbar.write(list(d.values()), table=True)

    def before_epoch(self, learn):
        learn.dl = progress_bar(learn.dl, leave=False, parent=self.mbar)

    def after_batch(self, learn):
        learn.dl.comment = f"{learn.loss:.3f}"
        if self.plot and hasattr(learn, "metrics") and learn.training:
            self.losses.append(learn.loss.item())
            if self.val_losses and self.i % self.periodicity == 0:
                x = [fc.L.range(self.losses), self.losses]
                steps = fc.L.range(learn.epoch).map(
                    lambda x: (x + 1) * len(learn.dls["train"])
                )
                y = [steps, self.val_losses]
                self.mbar.update_graph([x, y])
        self.i += 1

    def after_epoch(self, learn):
        if not learn.training:
            if self.plot and hasattr(learn, "metrics"):
                self.val_losses.append(learn.metrics.all_metrics["loss"].compute())
                x = [fc.L.range(self.losses), self.losses]
                steps = fc.L.range(learn.epoch + 1).map(
                    lambda x: (x + 1) * len(learn.dls["train"])
                )
                y = [steps, self.val_losses]
                self.mbar.update_graph([x, y])

# %% ../nbs/07_learner.ipynb 34
def to_cpu(x):
    if isinstance(x, Mapping):
        return {k: to_cpu(v) for k, v in x.items()}
    if isinstance(x, list):
        return [to_cpu(o) for o in x]
    if isinstance(x, tuple):
        return tuple(to_cpu(list(x)))
    res = x.detach().cpu()
    return res.float() if res.dtype == torch.float16 else res

# %% ../nbs/07_learner.ipynb 35
def fashion_mnist(bs=2048, **kwargs):
    """Helper to use fashion MNIST"""
    return tensorize_images(
        DataLoaders.from_hf("fashion_mnist", bs=bs, nworkers=4), **kwargs
    ).listify()

# %% ../nbs/07_learner.ipynb 42
class TrainLearner(Learner):
    """Sane training loop"""

    def predict(self):
        xb, yb = self.batch
        self.preds = self.model(xb)

    def get_loss(self):
        xb, yb = self.batch
        self.loss = self.loss_func(self.preds, yb)

    def backward(self):
        self.loss.backward()

    def step(self):
        self.opt.step()

    def zero_grad(self):
        self.opt.zero_grad()

# %% ../nbs/07_learner.ipynb 46
class MomentumCB(Callback):
    def __init__(self, momentum=0.85):
        self.momentum = momentum

    def zero_grad(self, learn):
        with torch.no_grad():
            for p in learn.model.parameters():
                p.grad *= self.momentum

# %% ../nbs/07_learner.ipynb 49
class LRFinderCB(Callback):
    """Find an apopriate learning rate by increasing it by a constant factor for each batch
    until the loss diverges"""

    def __init__(self, gamma=1.3, max_mult=3):
        fc.store_attr()

    def before_fit(self, learn):
        self.sched = ExponentialLR(learn.opt, self.gamma)
        self.lrs, self.losses = [], []
        self.min = math.inf

    def after_batch(self, learn):
        if not learn.training:
            raise CancelEpochException()
        self.lrs.append(learn.opt.param_groups[0]["lr"])
        loss = to_cpu(learn.loss)
        self.losses.append(loss)
        if loss < self.min:
            self.min = loss
        if math.isnan(loss) or (loss > self.min * self.max_mult):
            raise CancelFitException

        # Decays the learning rate of each parameter group by gamma every epoch.
        # https://pytorch.org/docs/stable/generated/torch.optim.lr_scheduler.ExponentialLR.html

        self.sched.step()

    def cleanup_fit(self, learn):
        fig, ax = plt.subplots()
        ax.plot(self.lrs, self.losses)
        ax.set_xscale("log")


@fc.patch
def lr_find(self: Learner, gamma=1.3, max_mult=3, start_lr=1e-5, max_epochs=10):
    self.fit(max_epochs, lr=start_lr, cbs=LRFinderCB(gamma=gamma, max_mult=max_mult))
    return self
