# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/05_convs.ipynb.

# %% auto 0
__all__ = ['def_device', 'conv', 'to_device', 'get_model', 'accuracy', 'fit', 'get_dls_from_dataset_dict', 'fashion_collate',
           'fashion_mnist']

# %% ../nbs/05_convs.ipynb 3
import os
import tempfile
from contextlib import contextmanager
from typing import Mapping

import datasets
import torch
import torch.nn.functional as F
import torchvision.transforms.functional as T
from datasets import load_dataset, load_from_disk
from torch import nn, optim, tensor
from torch.utils.data import DataLoader, default_collate
from tqdm import tqdm

from .utils import show_image, show_images

# %% ../nbs/05_convs.ipynb 18
def conv(ni, nf, ks=3, stride=2, act=True):
    conv_ = nn.Conv2d(ni, nf, stride=stride, kernel_size=ks, padding=ks // 2)
    if act:
        return nn.Sequential(conv_, nn.ReLU())
    else:
        return conv_

# %% ../nbs/05_convs.ipynb 20
def_device = (
    "mps"
    if torch.backends.mps.is_available()
    else "cuda"
    if torch.cuda.is_available()
    else "cpu"
)


def to_device(x, device=def_device):
    if isinstance(x, torch.Tensor):
        return x.to(device)
    if isinstance(x, Mapping):
        return {k: v.to(device) for k, v in x.items()}
    return type(x)(to_device(o, device) for o in x)

# %% ../nbs/05_convs.ipynb 22
def get_model():
    model = nn.Sequential(
        conv(1, 4),  # 14x14
        conv(4, 8),  # 7x7
        conv(8, 16),  # 4x4
        conv(16, 16),  # 2x2
        conv(16, 10, act=False),  # 1x1
        nn.Flatten(),
    )
    return model.to(def_device)


get_model()

# %% ../nbs/05_convs.ipynb 24
def accuracy(y, y_pred):
    n, _ = y.shape
    return (y.argmax(axis=1) == y_pred).sum() / n


def fit(epochs, model, loss_func, opt, train_dl, valid_dl, tqdm_=False):
    progress = tqdm if tqdm_ else lambda x: x
    for epoch in range(epochs):
        model.train()
        for batch in progress(train_dl):
            xb, yb = map(to_device, batch)
            loss = loss_func(model(xb), yb)
            loss.backward()
            opt.step()
            opt.zero_grad()

        model.eval()
        with torch.no_grad():
            tot_loss, tot_acc, count = 0.0, 0.0, 0
            for batch in progress(valid_dl):
                xb, yb = map(to_device, batch)
                pred = model(xb)
                n = len(xb)
                count += n
                tot_loss += loss_func(pred, yb).item() * n
                tot_acc += accuracy(pred, yb).item() * n

        print(
            f"{epoch=}, validation loss={tot_loss / count:.3f}, validation accuracy={tot_acc / count:.2f}"
        )
    return tot_loss / count, tot_acc / count


@contextmanager
def get_dls_from_dataset_dict(dsd, collate_fn=default_collate, bs=32):
    datasets.logging.disable_progress_bar()
    with tempfile.TemporaryDirectory() as tdir:
        dls = []
        for split in ["train", "test"]:
            dir_ = os.path.join(tdir, split)
            dsd[split].save_to_disk(dir_)
            ds = load_from_disk(dir_).with_format("torch")
            dl = DataLoader(ds, batch_size=bs, collate_fn=collate_fn, num_workers=8)
            dls.append(dl)
        yield dls

# %% ../nbs/05_convs.ipynb 25
def fashion_collate(examples):
    batch = default_collate(examples)
    xb = batch["image"][:, None, ...].float() / 255
    yb = batch["label"]
    return xb, yb


@contextmanager
def fashion_mnist(bs=256):
    dsd = load_dataset("fashion_mnist")
    with get_dls_from_dataset_dict(dsd, collate_fn=fashion_collate, bs=bs) as dls:
        yield dls
