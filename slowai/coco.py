# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/25_coco_a.ipynb.

# %% auto 0
__all__ = ['trn_preprocess_super_rez', 'tst_preprocess_super_rez', 'blur', 'postprocess_', 'get_coco_dataset_super_rez',
           'grayscale', 'get_coco_dataset_colorization', 'to_img', 'black_and_white', 'coco_2017_trn', 'crop_to_box',
           'preprocess_super_rez', 'get_coco_dataset', 'preprocess_colorization']

# %% ../nbs/25_coco_a.ipynb 2
import multiprocessing
from functools import partial
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
import torchvision.transforms.functional as TF
import torchvision.transforms.v2 as T
from datasets import Dataset
from PIL import Image
from tqdm import tqdm

from .learner import DataLoaders
from .tinyimagenet_a import denorm, fill, norm
from .utils import show_images

# %% ../nbs/25_coco_a.ipynb 6
def to_img(im):
    """Convert PIL image to numpy"""
    return np.array(im).astype(np.float32) / 255

# %% ../nbs/25_coco_a.ipynb 7
def black_and_white(im, viz=False, thresh=0.01):
    """Infer whether image is black and white by seeing how much
    the original image and a converted black-and-white version
    differ from one another"""
    if isinstance(im, str):
        im = Image.open(im)
    if im.mode == "L":
        return True
    im_color = im.convert("RGB")
    h, w = im_color.size
    im_bw = im_color.convert("L").convert("RGB")
    im_color, im_bw = map(to_img, (im_color, im_bw))
    delta = abs(im_color - im_bw).sum() / (h * w * 3)
    if viz:
        show_images([im_color], titles=[f"Delta: {delta:.4f}"])
    return delta < thresh

# %% ../nbs/25_coco_a.ipynb 14
def coco_2017_trn(fps=None, n=None, remove_bw=True):
    """Combine the image preprocessing logic and return a
    huggingface dataset"""
    if fps is None:
        fps = Path(fp).glob("**/*.jpg")
    if n:
        fps = fps[:n]
    ds = Dataset.from_dict({"image_fp": [str(fp) for fp in fps]})

    def open_images(examples):
        imgs = []
        for img_fp in examples["image_fp"]:
            img = Image.open(img_fp)
            imgs.append(img)
        return {"image": imgs}

    ds = ds.map(open_images, batched=True)
    ds = ds.train_test_split(test_size=0.1)
    return ds

# %% ../nbs/25_coco_a.ipynb 17
def crop_to_box(img: "Image"):
    width, height = img.size
    if width > height:
        new_width = height
        new_height = height
        left = (width - new_width) // 2
        top = 0
        right = left + new_width
        bottom = new_height
    else:
        new_width = width
        new_height = width
        left = 0
        top = (height - new_height) // 2
        right = new_width
        bottom = top + new_height
    img_cropped = img.crop((left, top, right, bottom))
    return img_cropped

# %% ../nbs/25_coco_a.ipynb 18
trn_preprocess_super_rez = [
    T.RandomHorizontalFlip(),
    T.ColorJitter(brightness=(0.35, 1.65), hue=(-0.05, 0.05)),
    T.RandomAffine(scale=(1.0, 1.2), degrees=(0, 20), fill=fill),
    crop_to_box,
    T.Resize((150, 150)),
    T.RandomCrop((128, 128)),
    T.PILToTensor(),
]
tst_preprocess_super_rez = [
    crop_to_box,
    T.Resize((150, 150)),
    T.CenterCrop((128, 128)),
    T.PILToTensor(),
]
blur = T.GaussianBlur((5, 9), (0.1, 5.0))
postprocess_ = T.Compose([T.ConvertImageDtype(torch.float), norm])

# %% ../nbs/25_coco_a.ipynb 19
def preprocess_super_rez(examples, pipe, extra_blur=False):
    pre = T.Compose(pipe)
    imgs = []
    for img in examples["image"]:
        img = img.convert("RGB")
        img_hr = pre(img)
        # Note that this resizing discards details but retains the image
        # dimensions, which makes it slightly easier to design the network
        img_lr = TF.resize(img_hr, (64, 64), antialias=True)
        if extra_blur:
            img_lr = blur(img_lr)
        img_lr = TF.resize(img_lr, (128, 128), antialias=False)
        img_hr, img_lr = map(postprocess_, (img_hr, img_lr))
        imgs.append((img_hr, img_lr))
    imgs_hr, imgs_lr = map(torch.stack, zip(*imgs))
    return {"image_high_rez": imgs_hr, "image_low_rez": imgs_lr}

# %% ../nbs/25_coco_a.ipynb 21
def get_coco_dataset(
    fac,
    trn,
    tst,
    fp="data/train2017",
    bs=512,
    n=None,
    columns=["image_low_rez", "image_high_rez"],
):
    fps = list(Path(fp).glob("**/*.jpg"))
    dsd = coco_2017_trn(fps, n=n)
    dsd["train"].set_transform(partial(fac, pipe=trn))
    dsd["train"] = dsd["train"].shuffle()
    dsd["test"] = (
        dsd["test"]
        .map(
            partial(fac, pipe=tst),
            batched=True,
            # Unused, no need to collate and waste time/compute
            remove_columns=["image"],
        )
        .with_format("torch")
    )

    return DataLoaders.from_dsd(dsd, bs=bs).listify(columns=columns)

# %% ../nbs/25_coco_a.ipynb 22
get_coco_dataset_super_rez = partial(
    get_coco_dataset,
    preprocess_super_rez,
    trn_preprocess_super_rez,
    tst_preprocess_super_rez,
)

# %% ../nbs/25_coco_a.ipynb 25
grayscale = T.Grayscale(num_output_channels=3)

# %% ../nbs/25_coco_a.ipynb 26
def preprocess_colorization(examples, pipe):
    pre = T.Compose(pipe)
    imgs = []
    for img in examples["image"]:
        img_color = pre(img.convert("RGB"))
        img_bw = grayscale(img_color)
        img_color, img_bw = map(postprocess_, (img_color, img_bw))
        imgs.append((img_color, img_bw))
    imgs_color, imgs_bw = map(torch.stack, zip(*imgs))
    return {"color": imgs_color, "bw": imgs_bw}

# %% ../nbs/25_coco_a.ipynb 27
get_coco_dataset_colorization = partial(
    get_coco_dataset,
    preprocess_colorization,
    trn_preprocess_super_rez,
    tst_preprocess_super_rez,
    columns=["bw", "color"],
)
