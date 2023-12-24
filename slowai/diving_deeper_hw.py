# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_diving_deeper_homework.ipynb.

# %% auto 0
__all__ = ['pipe', 'get_simple_pipe', 'get_stable_diffusion', 'StableDiffusionWithNegativePromptB',
           'StableDiffusionWithNegativePromptC']

# %% ../nbs/01_diving_deeper_homework.ipynb 2
from typing import List

import torch
from diffusers import LMSDiscreteScheduler, StableDiffusionPipeline
from tqdm import tqdm

from .overview import TORCH_DEVICE, StableDiffusion, decompress

# %% ../nbs/01_diving_deeper_homework.ipynb 6
pipe = None


def get_simple_pipe():
    pipe = StableDiffusionPipeline.from_pretrained("CompVis/stable-diffusion-v1-4")
    # Use a simple noising scheduler for the initial draft
    pipe.scheduler = LMSDiscreteScheduler(
        beta_start=0.00085,
        beta_end=0.012,
        beta_schedule="scaled_linear",
        num_train_timesteps=1000,
    )
    pipe = pipe.to(TORCH_DEVICE)
    pipe.enable_attention_slicing()
    return pipe


def get_stable_diffusion(cls=StableDiffusion):
    global pipe
    if pipe is None:
        pipe = get_simple_pipe()
    return cls(
        tokenizer=pipe.tokenizer,
        text_encoder=pipe.text_encoder,
        scheduler=pipe.scheduler,
        unet=pipe.unet,
        vae=pipe.vae,
    )

# %% ../nbs/01_diving_deeper_homework.ipynb 15
class StableDiffusionWithNegativePromptB(StableDiffusionWithNegativePromptA):
    def pred_noise(
        self, prompt_embedding, l, t, guidance_scale_pos, guidance_scale_neg
    ):
        latent_model_input = torch.cat([l] * 3)
        # Scale the initial noise by the variance required by the scheduler
        latent_model_input = self.scheduler.scale_model_input(latent_model_input, t)
        with torch.no_grad():
            noise_pred = self.unet(
                latent_model_input, t, encoder_hidden_states=prompt_embedding
            ).sample
        chunks = noise_pred.chunk(3)
        noise_pred_text_pos, noise_pred_uncond, noise_pred_text_neg = chunks
        return (
            noise_pred_uncond
            + guidance_scale_pos * (noise_pred_text_pos - noise_pred_uncond)
            - guidance_scale_neg * (noise_pred_text_neg - noise_pred_uncond)
        )

# %% ../nbs/01_diving_deeper_homework.ipynb 18
class StableDiffusionWithNegativePromptC(StableDiffusionWithNegativePromptB):
    def denoise(
        self,
        prompt_embedding,
        guidance_scale_pos,
        guidance_scale_neg,
        l,  # latents
        t,  # timestep
        i,  # global progress
    ):
        noise_pred = self.pred_noise(
            prompt_embedding, l, t, guidance_scale_pos, guidance_scale_neg
        )
        return self.scheduler.step(noise_pred, t, l).prev_sample

    @torch.no_grad()
    def __call__(
        self,
        prompt,
        negative_prompt,
        guidance_scale=7.5,
        neg_guidance_scale=2,
        n_inference_steps=30,
        as_pil=False,
    ):
        prompt_embedding = self.embed_prompt(prompt, negative_prompt)
        l = self.init_latents()
        self.init_schedule(n_inference_steps)
        # Note that the time steps aren't neccesarily 1, 2, 3, etc
        for i, t in tqdm(enumerate(self.scheduler.timesteps), total=n_inference_steps):
            # workaround for ARM Macs where float64's are not supported
            t = t.to(torch.float32).to(TORCH_DEVICE)
            l = self.denoise(
                prompt_embedding, guidance_scale, neg_guidance_scale, l, t, i
            )
        return decompress(l, self.vae, as_pil=as_pil)