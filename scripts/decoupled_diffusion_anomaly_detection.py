"""
anomaly detection with guided diffusion
"""

import argparse
import os

os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import numpy as np
import torch as th
import torch.distributed as dist
import torch.nn.functional as F
import tqdm

from guided_diffusion import dist_util, logger, utils
from guided_diffusion.script_util import (
    add_dict_to_argparser,
    args_to_dict,
)
from guided_diffusion.anomaly_utils import create_decoupled_model_and_diffusion, decoupled_diffusion_and_diffusion_defaults
from guided_diffusion.dataset import load_data

def main():
    args = create_argparser().parse_args()
    args.__dict__.update(utils.load_parameters(args))
    
    th.random.manual_seed(args.random_seed)
    
    dist_util.setup_dist()
    logger.configure(dir=args.output_dir)

    logger.log("creating model and diffusion...")
    model, diffusion = create_decoupled_model_and_diffusion(
        **args_to_dict(args, decoupled_diffusion_and_diffusion_defaults().keys()),
    )
    model.load_state_dict(
        dist_util.load_state_dict(args.model_path, map_location="cpu")
    )
    
    model.to(dist_util.dev())
    if args.use_fp16:
        model.convert_to_fp16()
    model.eval()

    if args.shifting_z:
        state_dict = dist_util.load_state_dict(args.linear_path, map_location="cpu")
        w = state_dict["weight"].to(dist_util.dev())
        b = state_dict["bias"].to(dist_util.dev())
        median = th.Tensor(np.load(args.median_path)['lgs_healthy']).to(dist_util.dev())
        z_state = np.load(args.z_state_path)
        z_mean, z_std = th.Tensor(z_state['z_mean']).to(dist_util.dev()), th.Tensor(z_state['z_std']).to(dist_util.dev())
        
    data = load_data(
            data_dir=args.data_dir,
            batch_size=args.batch_size,
            dataset=args.dataset,
            deterministic=False,
            limited_num=int(3200/args.batch_size) if args.calcu_thresh else -1,
            test=True,
        )
    
    def model_fn(x, t, z):
        return model.predict_with_Z(x, t, z)
    
    if args.shifting_z:
        def shiftingZ(z:th.Tensor):
            z = (z - z_mean)/z_std
            #s = (median - b - th.mm(z, w.transpose(0,1)))/(th.mm(w,(w.transpose(0,1))))
            s = (args.anomaly_score - b - th.mm(z, w.transpose(0,1)))/(th.mm(w,(w.transpose(0,1))))
            z = z + s * w
            z = z * z_std + z_mean

            return z
    else:
        def shiftingZ(z:th.Tensor):
            return z
    logger.log("testing...")
    
    all_images = []
    sample_fn = (
        diffusion.ddpm_anomaly_detection if not args.use_ddim else diffusion.ddim_anomaly_detection
    )
    
    start = th.cuda.Event(enable_timing=True)
    end = th.cuda.Event(enable_timing=True)
    start.record()
    with th.no_grad():
        for imgs, extra in tqdm.tqdm(data):
            model_kwargs = {}
            # classes = th.randint(
            #     low=0, high=1, size=(args.batch_size,), device=dist_util.dev()
            # )
            encoder_kwargs = {}
            if args.class_cond:
                encoder_kwargs['y'] = th.zeros(size=(args.batch_size,), device=dist_util.dev(), dtype=th.int64)
            
            img_batch = imgs.to(dist_util.dev())
            
            z = model.get_embbed(img_batch, **encoder_kwargs)
            model_kwargs["z"] = shiftingZ(z)

            
            sample = sample_fn(
                model_fn,
                img_batch,
                clip_denoised=args.clip_denoised,
                model_kwargs=model_kwargs,
                device=dist_util.dev(),
            )["generated_image"]
            sample = float2uint(sample)
            
            seg = float2uint(extra["seg"], rescale=False).to(dist_util.dev())
            img_batch = float2uint(img_batch, rescale=True)

            save_sample = th.concat([img_batch,seg,sample],dim=3)
            
            gathered_samples = [th.zeros_like(save_sample) for _ in range(dist.get_world_size())]
            dist.all_gather(gathered_samples, save_sample)  # gather not supported with NCCL
            all_images.extend([save_sample.cpu().numpy() for save_sample in gathered_samples])
    
    arr = np.concatenate(all_images, axis=0)
    end.record()
    th.cuda.synchronize()
    th.cuda.current_stream().synchronize()
    time_taken = start.elapsed_time(end)
    logger.log(f"Take {time_taken}s to test {len(arr)} samples")
    
    if dist.get_rank() == 0:
        logger.log(f"saving to {logger.get_dir()}")
        for idx, sample in enumerate(arr):

            save_arr = arr[idx][None,...]
            
            out_path = os.path.join(logger.get_dir(), f"samples_{idx}.npy")
            np.save(out_path, save_arr)

    dist.barrier()
    logger.log("anomaly detection complete")


def create_argparser():
    defaults = dict(
        clip_denoised=True,
        batch_size=16,
        use_ddim=False,
        model_path="",
        classifier_path="",
        classifier_scale=1.0,
        max_t=-1,
        output_dir="./output/anomaly_detection",
        dataset="Brats2020",
        data_dir = "/home/xuehong/Datasets/Brats_Processed_Split/val",
        z_state_path="",
        linear_path="",
        median_path="",
        random_seed=1126,
        shifting_z=False,
        anomaly_score=-3,
        calcu_thresh=False,
    )
    defaults.update(decoupled_diffusion_and_diffusion_defaults())
    parser = argparse.ArgumentParser()
    parser.add_argument(f"--cfg", default="classifier_1", type=str)
    add_dict_to_argparser(parser, defaults)
    return parser

def float2uint(input:th.Tensor, rescale=True):
    if rescale:
        input = ((input + 1) * 127.5).clamp(0, 255).to(th.uint8)
    else:
        input = input.clamp(0, 255).to(th.uint8)   
    input = input.permute(0, 2, 3, 1)
    input = input.contiguous()
    return input

if __name__ == "__main__":
    main()
