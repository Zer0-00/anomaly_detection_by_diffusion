import csv
from collections import defaultdict
import tqdm
from matplotlib import pyplot as plt
import numpy as np
import os
import cv2

from metrics import nonzero_masking, remove_noise

def evaluate_training(progress_dir, save_dir):
    """
    Visualize the change of metrics in the training process.
    Args:
        progress_dir: progress csv file path
    """
    #figure configuration
    num_columns = 4
    figsize = (12,10)
    warm_up_steps = 4000        #metrics of steps before warmup will not be showed in figure if data shifts too much from center
    skip_metric = ["step", "samples"]        #metrics that are skipped
    
    #reading data
    warm_up_idx = 0
    metrics = defaultdict(list)
    with open(progress_dir, 'r') as f:
        reader = csv.DictReader(f)
        for row in tqdm.tqdm(reader):
            if int(row["step"]) < warm_up_steps:
                warm_up_idx += 1
                
            for k, v in row.items():
                metrics[k].append(float(v))
    
    #visualize
    num_rows = int(np.ceil((len(metrics) - len(set(skip_metric) & set(metrics.keys()))) / num_columns))
    plt.figure(figsize=figsize)
    
    idx = 0
    for metric_name in metrics:
        if metric_name in skip_metric:
            continue
        else:
            idx += 1
            plt.subplot(num_rows, num_columns, idx)
            plt.plot(metrics["step"], metrics[metric_name])
            plt.title(metric_name)
            plt.xlabel("step")
            plt.ylim((min(metrics[metric_name][warm_up_idx:])), max(metrics[metric_name][warm_up_idx:]))        
            
    plt.savefig(save_dir, dpi=600)
    plt.close()
    
def evaluate_image(image_path, save_dir):
    data = np.load(image_path)
    
    def mask_fn(pred, mask, return_thresh=False):
        masked_pred = pred[np.where(mask > 0)].reshape(1, -1)
        masked_pred = (masked_pred * 255.0).astype(np.uint8).squeeze()
        thresh ,_ = cv2.threshold(masked_pred, 0, 255, cv2.THRESH_OTSU)
        thresh = thresh/255.0
        pred = (pred > thresh) * 1.0
        if return_thresh:
            return thresh, pred
        else:
            return pred
    img = data[np.newaxis,0,:,:,:4]*1.0/255.0
    seg = np.expand_dims(data[0,:,:,4], axis=(0,-1))
    generated = data[np.newaxis,0,:,:,5:]*1.0/255.0
    pred = np.expand_dims(np.mean(np.sqrt((generated-img)**2), axis=3), axis = -1)
    pred = remove_noise(pred)
    #change from (0,255) to (0,1)
    pred, mask = nonzero_masking(img, pred, return_mask=True)
    thresh, pred_seg = mask_fn(pred, mask, return_thresh=True)
    
    pred_modality = remove_noise(np.sqrt((generated-img)**2))
    
    for i in range(4):
        plt.subplot(4,4,i + 1)
        plt.imshow(img[0,:,:,i].squeeze(),cmap='gray', vmin=0.0, vmax=1.0)
        plt.axis('off')
        plt.title(f'image(channel{i})')
    for i in range(4):
        plt.subplot(4,4,i + 5)
        plt.imshow(generated[0,:,:,i].squeeze(), cmap='gray', vmin=0.0, vmax=1.0)
        plt.axis('off')
        plt.title(f'generated(channel{i})')
    for i in range(4):
        plt.subplot(4,4,i + 9)
        plt.imshow(pred_modality[0,:,:,i].squeeze(), cmap='gray')
        plt.axis('off')
        plt.title(f'pred(channel{i})')
    plt.subplot(4,4,14)
    plt.imshow((seg > 0 * 1.0).squeeze(),cmap='gray', vmin=0.0, vmax=1.0)
    plt.axis('off')
    plt.title('ground truth')
    plt.subplot(4,4,15)
    plt.imshow(pred_seg.squeeze(), cmap='gray', vmin=0.0, vmax=1.0)
    plt.axis('off')
    plt.title('segmentation')
    plt.subplot(4,4,16)
    plt.imshow((pred * pred_seg).squeeze(), cmap='Spectral_r', vmin=0.0, vmax=1.0)
    plt.colorbar()
    #plt.imshow(img.squeeze().astype(np.uint), cmap='gray', alpha=0.5)
    plt.axis('off')
    plt.title('image+segmentation')
    plt.savefig(save_dir)
    print("threshold:", thresh)
    plt.close
    
def evaluate_z(data_path, output_path):
    data = np.load(data_path)
    
    zs = data['all_zs']
    labels = data['all_labels'].squeeze()
    
    from sklearn import manifold
    tsne = manifold.TSNE()
    z_tsne = tsne.fit_transform(zs)
    
    z_min, z_max = z_tsne.min(0), z_tsne.max(0)
    z_norm = (z_tsne - z_min) / (z_max - z_min)
    
    normal_mask = np.where(labels == 0)
    abnormal_mask = np.where(labels == 1)
    
    plt.figure(figsize=(8,8))
    plt.subplot(1,3,1)
    plt.scatter(z_norm[normal_mask,0], z_norm[normal_mask,1], c='b')
    plt.scatter(z_norm[abnormal_mask,0], z_norm[abnormal_mask,1], c='r')
    plt.axis([0,1,0,1])
    plt.title('all')
    plt.axis("off")
    plt.subplot(1,3,2)
    plt.scatter(z_norm[normal_mask,0], z_norm[normal_mask,1], c='b')
    plt.axis("off")
    plt.axis([0,1,0,1])
    plt.title('Normal')
    plt.subplot(1,3,3)
    plt.scatter(z_norm[abnormal_mask,0], z_norm[abnormal_mask,1], c='r')
    plt.axis("off")
    plt.axis([0,1,0,1])
    plt.title('abnormal')
    save_dir = os.path.join(output_path, 'tsne.jpg')
    plt.savefig(save_dir)
    plt.close()
    
    # normal_meanZ = zs[normal_mask].mean(axis=0)
    # abnormal_meanZ = zs[abnormal_mask].mean(axis=0)
    
    # save_dir = os.path.join(output_path, 'templates')
    # np.savez(save_dir, normalZ=normal_meanZ, abnormalZ=abnormal_meanZ)
    
if __name__ == '__main__':
    # progress_dir = "output/configs3/diffusion/progress.csv"
    # output_dir = "output/configs3/diffusion/progress.png"
    # evaluate_training(progress_dir,output_dir)
    
    evaluate_image("output/configs3/anomaly_detection/val/samples_24.npy", "output/configs3/anomaly_detection/val/samples_24.png")
    
    #evaluate_z("output/configs3/zGenerate/zs_and_labels.npz", "output/configs3/zGenerate/")