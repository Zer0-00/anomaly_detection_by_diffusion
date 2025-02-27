import torch
from sklearn.metrics import roc_auc_score
import numpy as np
from typing import Union
import os
import csv
from functools import partial
from tqdm import tqdm

ImageClass = Union[torch.Tensor,np.ndarray]

def dice_coeff(
    targets:ImageClass, 
    images:ImageClass,
    mask_fn=lambda x:x,
    epsilon=1e-6
):
    """
    calculate dice coefficient

    Args:
        images (torch.Tensor|np.ndarray): input image of (N,1,H,W) or (N,H,W,1)
        targets (torch.Tensor|np.ndarry): ground truth, should share the same shape as input
        epsilon (optional): a small number added to the denominator. Defaults to 1e-6.
    """
    assert images.shape == targets.shape and type(images) == type(targets),\
         "the input and target images should share the same shape and type"
    dice = 0
    dot_fn = torch.dot if type(images) == torch.Tensor else np.dot
    for image, target in zip(images, targets):
        image = mask_fn(image)
        dot = dot_fn(image.reshape(-1), target.reshape(-1))
        sum = image.sum() + target.sum()
        dice += (2 * dot + epsilon) / (sum + epsilon)
        
    dice = dice / images.shape[0]
    return dice

def region_specific_metrics(
    targets:ImageClass, 
    images:ImageClass, 
    func,
    region_type='WT',
    **func_kwargs
):
    assert region_type in ["ET", "TC", "WT"], "region type should be one of ET, TC, WT"
    if region_type == 'ET':
        masks = (targets == 1) * 1
    elif region_type == "TC":
        masks = (((targets == 1) + (targets == 4)) > 0) * 1
    else:
        masks = (((targets == 1) + (targets == 2) + (targets == 4))) > 0 * 1
        
    return func(masks, images, **func_kwargs)

def AUROC(
    targets:ImageClass, 
    images:ImageClass,
    threshold=200, 
):
    """
    calculate AUROC

    Args:
        images (torch.Tensor|np.ndarray): input image of (N,1,H,W) or (N,H,W,1)
        targets (torch.Tensor|np.ndarray): ground truth, should share the same shape as input
        "minimal number of pixel for a anomaly image to be considered as anomalous"
    """
    assert images.shape == targets.shape and type(images) == type(targets),\
         "the input and target images should share the same shape and type"
         
    score = 0
    for image, target in zip(images, targets):
        if isinstance(images, torch.Tensor):     
            target = target.detach().cpu().to(torch.uint8).numpy().flatten().squeeze()
            image = image.detach().cpu().numpy().flatten().squeeze()
        else:
            target = target.flatten().squeeze()
            image = image.flatten().squeeze()
        if target.sum() <= threshold:
            score = -1
        else: 
            score+=(roc_auc_score(target, image))
        
    return score/images.shape[0]

def min_max_scale(image:ImageClass):
    return (image - image.min())/(image.max()-image.min())

def nonzero_masking(images:ImageClass, targets:ImageClass, return_mask=False):
    """
    masking targets according to the non-zero pixels of images
     
    p.s: non-zero is not mean the value of pixels is zero(sometimes tensor can be among [-1,1]),
         so pixels are reagarded as non-zero when the value of them is larger than images.min()
    
    Args:
        images (torch.Tensor|np.ndarray): input image of (N,1,H,W) or (N,H,W,1)
        targets (torch.Tensor|np.ndarray): ground truth, should share the same shape as input
        return_mask (bool): whether to return the mask
    """
    assert type(images) == type(targets),\
        "the input and target images should share the same and type"
    if isinstance(images, torch.Tensor):
        sum_kwargs = {"dim":1, "keepdim": True}
        assert images.shape[2:] == targets.shape[2:],\
            f"the input and target images should share the same shape(H,W) get image: {images.shape[2:]} and target: {targets.shape[2:]} "  
            
    else:
        #numpy
        assert images.shape[1:3] == targets.shape[1:3],\
            f"the input and target images should share the same shape(H,W), get image: {images.shape[1:3]} and target: {targets.shape[1:3]}"
        sum_kwargs = {"axis":3, "keepdims": True}
            
    mask = ((images > images.min() * 1.0).sum(**sum_kwargs) == 4) * 1.0
    
    targets = targets * mask + targets.min() * (1 - mask)
    
    if return_mask:
        return targets, mask
    else:
        return targets
        
def remove_noise(image):
    mask = (image >= np.percentile(image, 1)) * (image <= np.percentile(image, 99)) * 1.0
    return image * mask

class BratsEvaluator():
    def __init__(
        self,
        data_folder,
        metrics,
        mask_fn=None,
    ):  
        self.data_folder = data_folder
        self.metrics = metrics
        self.mask_fn = (lambda x: x) if mask_fn is None else mask_fn
        
        self.data_files = [file_name for file_name in os.listdir(self.data_folder) if file_name.endswith(".npy")]
        
    def evaluate_images(self,output_dir, store_data=True, use_tqdm=False):
        
        if store_data:
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            csvfile = open(os.path.join(output_dir,"metrics.csv"), 'w', newline='')
            fieldnames = ['file_name', 'threshold']+list(self.metrics.keys())
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
                
        metrics_imgs = {metric:[] for metric in self.metrics}
        
        iterf = tqdm(self.data_files) if  use_tqdm else self.data_files
              
        for file_name in iterf:
            file_dir = os.path.join(self.data_folder, file_name)
            data = np.load(file_dir)
            
            img = data[np.newaxis,0,:,:,:4]*1.0/255.0
            seg = np.expand_dims(data[0,:,:,4], axis=(0,-1))
            generated = data[np.newaxis,0,:,:,5:]*1.0/255.0
            pred = np.expand_dims(np.mean(np.sqrt((generated-img)**2), axis=3), axis = -1)
            
            
            pred = nonzero_masking(img, pred)
            
            metrics_img = {metric: metric_fn(seg,pred) for metric, metric_fn in self.metrics.items()}
            metrics_img["file_name"] = file_name
            
            #filter the images that have no anomalies
            if metrics_img["AUROC_WT"] == -1:
                continue
            
            if store_data:
                writer.writerow(metrics_img)
            
            for key in metrics_imgs:
                metrics_imgs[key].append(metrics_img[key])
        
        if store_data:
            csvfile.close()
                        
        metrics = {k:(np.mean(v), np.std(v)) for k,v in metrics_imgs.items()}
        return metrics
                
    def finding_threshold(self, use_tqdm=False):
                
        metrics_imgs = {metric:[] for metric in self.metrics}
        metrics_imgs['threshold'] = []
        
        iterf = tqdm(self.data_files) if  use_tqdm else self.data_files
        
        max_min_dict = {"max_DICE":0, "max_DICE_file":None,"max_DICE_thresh":0,"min_DICE":1, "min_DICE_file":None, "min_DICE_thresh":0}
        for file_name in iterf:
            file_dir = os.path.join(self.data_folder, file_name)
            data = np.load(file_dir)
            
            img = data[np.newaxis,0,:,:,:4]*1.0/255.0
            seg = np.expand_dims(data[0,:,:,4], axis=(0,-1))
            generated = data[np.newaxis,0,:,:,5:]*1.0/255.0
            pred = np.expand_dims(np.mean(np.sqrt((generated-img)**2), axis=3), axis = -1)
            pred = remove_noise(pred)
            pred, mask = nonzero_masking(img, pred, return_mask=True)
            thresh, _ = self.mask_fn(pred, mask, return_thresh=True)
            
            mask_fn = partial(self.mask_fn, mask=mask.squeeze(0))

            self.metrics["DICE_WT"] = partial(region_specific_metrics, func=dice_coeff, region_type="WT", mask_fn=mask_fn)
            
            metrics_img = {metric: metric_fn(seg,pred) for metric, metric_fn in self.metrics.items()}
            metrics_img["file_name"] = file_name
            metrics_img['threshold'] = thresh
            
            #filter the images that have no anomalies
            if metrics_img["AUROC_WT"] == -1:
                continue
            
            
            for key in metrics_imgs:
                metrics_imgs[key].append(metrics_img[key])
                
            if metrics_img["DICE_WT"] > max_min_dict["max_DICE"]:
                max_min_dict["max_DICE"] = metrics_img["DICE_WT"]
                max_min_dict["max_DICE_file"] = metrics_img["file_name"]
                max_min_dict["max_DICE_thresh"] = metrics_img['threshold']
            if metrics_img["DICE_WT"] < max_min_dict["min_DICE"]:
                max_min_dict["min_DICE"] = metrics_img["DICE_WT"]
                max_min_dict["min_DICE_file"] = metrics_img["file_name"]
                max_min_dict["min_DICE_thresh"] = metrics_img['threshold']
                        
        metrics = {k:(np.mean(v), np.std(v)) for k,v in metrics_imgs.items()}
        return metrics, max_min_dict
                
def finding_threshold(data_folder, output_dir):
    """finging threshold based on DICE by otsu

    Args:
        data_folder: generated results folder
        output_dir: output directory
    """
    import cv2
    import pandas as pd
    
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
    metrics = {
        "DICE_WT": partial(region_specific_metrics, func=dice_coeff, region_type="WT", mask_fn=mask_fn),
        "AUROC_WT": partial(region_specific_metrics, func=AUROC, region_type="WT"),
    }
    
    evaluator = BratsEvaluator(
        data_folder=data_folder,
        metrics=metrics,
        mask_fn=mask_fn
    )
    metrics_thresh, max_min_dict = evaluator.finding_threshold(use_tqdm=True)
    metrics_output = {}
    for k,v in metrics_thresh.items():
        metrics_output[k+'(Mean)'] = v[0]
        metrics_output[k+'(Std)'] = v[1]
    metrics_output.update(max_min_dict)
    df = pd.DataFrame(metrics_output, index=[0])
    output_path = os.path.join(output_dir, "total.csv")
    df.to_csv(output_path)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_rows", None)
    print(df)
    
def using_thresh(data_folder, output_dir, thresh=0.0817678607279089):
    import pandas as pd
    
    metrics_threshs = {'threshold':thresh}
    
    def mask_fn(pred):
        return (pred >= metrics_threshs['threshold']) * 1.0
    
    metrics = {
        "DICE_WT": partial(region_specific_metrics, func=dice_coeff, region_type="WT", mask_fn=mask_fn),
        "AUROC_WT": partial(region_specific_metrics, func=AUROC, region_type="WT"),
        }
    
    evaluator = BratsEvaluator(
        data_folder=data_folder,
        metrics=metrics,
    )

    metrics_thresh = evaluator.evaluate_images(output_dir, store_data=False, use_tqdm=True)
    for k,v in metrics_thresh.items():
        metrics_threshs[k+'(Mean)'] = v[0]
        metrics_threshs[k+'(Std)'] = v[1]
            
    df = pd.DataFrame(metrics_threshs, index=[0])
    output_path = os.path.join(output_dir, "total.csv")
    df.to_csv(output_path)
    print(df)
    
if __name__ == "__main__":
    #using_thresh('output/configs4/anomaly_detection/val','output/configs4/anomaly_detection', thresh=0.03933)
    finding_threshold('output/configs3/anomaly_detection/val','output/configs3/anomaly_detection/val')