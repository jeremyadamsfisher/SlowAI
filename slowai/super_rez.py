# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/24_super_resolution.ipynb.

# %% auto 0
__all__ = ['get_imagenet_super_rez_dls', 'Conv', 'ResidualConvBlock', 'UpsamplingResidualConvBlock', 'KaimingMixin', 'train',
           'viz', 'AutoEncoder', 'TinyUnet', 'TinyImageResNet4', 'initialize_unet_weights_with_clf_weights',
           'TinyUnetWithCrossConvolutions']

# %% ../nbs/24_super_resolution.ipynb 3
from functools import partial

import matplotlib.pyplot as plt
import torch
import torch.nn.functional as F
import torchvision.transforms as T
import torchvision.transforms.functional as TF
from torch import nn
from torch.nn import init
from torch.optim import lr_scheduler
from torchmetrics.classification import MulticlassAccuracy

from .convs import def_device
from slowai.learner import (
    DataLoaders,
    DeviceCB,
    MetricsCB,
    ProgressCB,
    TrainLearner,
)
from .sgd import BatchSchedulerCB
from slowai.tinyimagenet_a import (
    denorm,
    fill,
    lr_find,
    norm,
    tiny_imagenet_dataset_dict,
)
from .utils import show_images

# %% ../nbs/24_super_resolution.ipynb 7
def get_imagenet_super_rez_dls(bs=512):
    dsd = tiny_imagenet_dataset_dict()
    dsd["train"].set_transform(partial(preprocess, pipe=preprocess_trn, erase=True))
    dsd["train"] = dsd["train"].shuffle()
    dsd["test"] = (
        dsd["test"]
        .map(
            partial(preprocess, pipe=preprocess_tst, erase=False),
            batched=True,
            # We need to remove images because some are black and white, causing
            # collation errors. Note that this column is unused, but it still
            # causes problems because it's available but not compatible
            remove_columns=["image"],
        )
        .with_format("torch")
    )
    columns = ["image_low_rez", "image_high_rez"]
    return DataLoaders.from_dsd(dsd, bs=bs).listify(columns=columns)

# %% ../nbs/24_super_resolution.ipynb 11
class Conv(nn.Conv2d):
    def __init__(self, c_in, c_out, stride=2, ks=3, **kwargs):
        super().__init__(
            c_in,
            c_out,
            stride=stride,
            kernel_size=ks,
            padding=ks // 2,
            **kwargs,
        )

# %% ../nbs/24_super_resolution.ipynb 12
class ResidualConvBlock(nn.Module):
    """Convolutional block with residual links without a final activation"""

    def __init__(self, c_in, c_out, stride=2, ks=3, with_final_activation=True):
        super().__init__()
        self.stride = stride
        self.c_in = c_in
        self.c_out = c_out
        self.with_final_activation = with_final_activation
        # Non-residual circuit
        # -- Note that the bias term is False because the normalization term makes
        # -- this redundant. See: https://tinyurl.com/mutwfn32
        self.convs = nn.Sequential(
            Conv(c_in, c_out, bias=False, stride=1, ks=ks),
            nn.BatchNorm2d(c_out),
            nn.ReLU(),
            Conv(c_out, c_out, bias=False, stride=stride, ks=ks),
            nn.BatchNorm2d(c_out),
        )
        # Residual circuit
        self.id_conv = nn.Conv2d(c_in, c_out, stride=1, kernel_size=1)

    def forward(self, x):
        x_orig = x.clone()
        # Non-residual circuit
        x = self.convs(x)
        # Residual circuit
        if self.stride == 2:
            x_orig = F.avg_pool2d(x_orig, kernel_size=2, ceil_mode=True)
        elif self.stride > 2:
            raise ValueError
        else:
            assert self.stride == 1
        if self.c_in != self.c_out:
            x_orig = self.id_conv(x_orig)
        # Combine circuits
        x += x_orig
        if self.with_final_activation:
            x = F.relu(x)
        return x

# %% ../nbs/24_super_resolution.ipynb 13
class UpsamplingResidualConvBlock(ResidualConvBlock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert self.stride == 1
        self.upsampler = nn.UpsamplingNearest2d(scale_factor=2)

    def forward(self, x):
        x = self.upsampler(x)
        x = super().forward(x)
        return x

# %% ../nbs/24_super_resolution.ipynb 14
class KaimingMixin:
    @staticmethod
    def init_kaiming(m):
        if isinstance(m, (nn.Conv1d, nn.Conv2d, nn.Conv3d)):
            init.kaiming_normal_(m.weight)

    @classmethod
    def kaiming(cls, *args, **kwargs):
        model = cls(*args, **kwargs)
        model.apply(cls.init_kaiming)
        return model

# %% ../nbs/24_super_resolution.ipynb 16
def train(
    model, dls, lr=4e-3, n_epochs=25, extra_cbs=[MetricsCB()], loss_fn=F.mse_loss
):
    T_max = len(dls["train"]) * n_epochs
    scheduler = BatchSchedulerCB(lr_scheduler.OneCycleLR, max_lr=lr, total_steps=T_max)
    cbs = [
        DeviceCB(),
        ProgressCB(plot=True),
        scheduler,
        *extra_cbs,
    ]
    learner = TrainLearner(
        model,
        dls,
        loss_fn,
        lr=lr,
        cbs=cbs,
        opt_func=partial(torch.optim.AdamW, eps=1e-5),
    )
    learner.fit(n_epochs)
    return model

# %% ../nbs/24_super_resolution.ipynb 17
def viz(xb, yb, yp, n=3):
    fig, axs = plt.subplots(n, 3)
    for i in range(n):
        for ax, im, title in [
            (axs[i, 0], yb[i, ...], "Ground Truth"),
            (axs[i, 1], xb[i, ...], "Downsampled"),
            (axs[i, 2], yp[i, ...], "Upsampled"),
        ]:
            im = denorm(im).permute(1, 2, 0).clip(0.0, 1.0)
            ax.imshow(im)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set(title=title)
    fig.tight_layout()

# %% ../nbs/24_super_resolution.ipynb 19
class AutoEncoder(nn.Sequential, KaimingMixin):
    """Project into a hidden space and reproject into the original space"""

    def __init__(self, nfs: list[int] = (32, 64, 128, 256, 512, 1024)):
        layers = [ResidualConvBlock(3, nfs[0], ks=5, stride=1)]
        cs = list(zip(nfs, nfs[1:]))
        for c_in, c_out in cs:
            l = ResidualConvBlock(c_in, c_out, stride=2)
            layers.append(l)
        for c_out, c_in in reversed(cs):
            l = UpsamplingResidualConvBlock(c_in, c_out, stride=1)
            layers.append(l)
        l = ResidualConvBlock(nfs[0], 3, stride=1, with_final_activation=False)
        layers.append(l)
        super().__init__(*layers)

# %% ../nbs/24_super_resolution.ipynb 28
class TinyUnet(nn.Module, KaimingMixin):
    def __init__(
        self,
        nfs: list[int] = (32, 64, 128, 256, 512, 1024),
        n_blocks=(3, 2, 2, 1, 1),
    ):
        super().__init__()
        self.start = ResidualConvBlock(3, nfs[0], ks=5, stride=1)
        cs = list(zip(nfs, nfs[1:]))
        self.downsamplers = nn.ModuleList()
        for c_in, c_out in cs:
            ld = ResidualConvBlock(c_in, c_out, stride=2)
            self.downsamplers.append(ld)
        self.upsamplers = nn.ModuleList()
        for c_out, c_in in reversed(cs):
            lu = UpsamplingResidualConvBlock(c_in, c_out, stride=1)
            self.upsamplers.append(lu)
        self.final = ResidualConvBlock(nfs[0], 3, stride=1, with_final_activation=False)

    def forward(self, x):
        x = self.start(x)
        x_orig = x.clone()
        xs = []
        for l in self.downsamplers:
            x = l(x)
            xs.append(x.clone())
        for xu, l in zip(reversed(xs), self.upsamplers):
            x = l(x + xu)
        x = self.final(x + x_orig)
        return x

# %% ../nbs/24_super_resolution.ipynb 36
class TinyImageResNet4(nn.Sequential, KaimingMixin):
    """Convolutional classification model"""

    def __init__(self, nfs: list[int] = (32, 64, 128, 256, 512, 1024)):
        self.conv_layers = [ResidualConvBlock(3, nfs[0], ks=5, stride=1)]
        for c_in, c_out in zip(nfs, nfs[1:]):
            self.conv_layers.append(ResidualConvBlock(c_in, c_out, stride=2))
        clf_head = (
            nn.AdaptiveAvgPool2d((1, 1)),
            nn.Flatten(),
            nn.Linear(1024, 200),
        )
        super().__init__(*self.conv_layers, *clf_head)

# %% ../nbs/24_super_resolution.ipynb 50
def initialize_unet_weights_with_clf_weights(c, unet):
    l0, *ls, _, _, _ = c
    unet.start.load_state_dict(l0.state_dict())
    for lc, lu in zip(ls, unet.downsamplers):
        lu.load_state_dict(lc.state_dict())
    return unet

# %% ../nbs/24_super_resolution.ipynb 56
class TinyUnetWithCrossConvolutions(TinyUnet):
    def __init__(
        self,
        nfs: list[int] = (32, 64, 128, 256, 512, 1024),
        n_blocks=(3, 2, 2, 1, 1),
    ):
        super().__init__(nfs, n_blocks)
        self.cross_convs = nn.ModuleList()
        for c_in in nfs[1:]:
            cross_conv = ResidualConvBlock(c_in, c_in, stride=1)
            self.cross_convs.append(cross_conv)

    def forward(self, x):
        x = self.start(x)
        x_orig = x.clone()
        xs = []
        for l in self.downsamplers:
            x = l(x)
            xs.append(x.clone())
        for xu, l, cc in zip(reversed(xs), self.upsamplers, reversed(self.cross_convs)):
            x = l(x + cc(xu))
        x = self.final(x + x_orig)
        return x
