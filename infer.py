# Built-in
from operator import pos
import os
import argparse

# Libs
import albumentations as A
from albumentations.pytorch import ToTensorV2
from natsort import natsorted
import numpy as np
from skimage import io, measure
from tqdm import tqdm

# Own modules
from mrs_utils import misc_utils, eval_utils
from network import network_io, network_utils
from make_file_list import make_file_list
# Settings

GPU = 0
# !!!CHANGE THIS!!! The folder where the images are stored
general_folder = r'data_raw/Exp1_1_resolution_buckets/test/d1/images'
# In case under the general folder you would like to evaluate on another
data_specific_folder = r'.'
# !!!CHANGE THIS!!! Your model directory here
MODEL_DIR = '../models/catalyst/loss_weight/catalystloss_weight=0.5/ecresnet50_dcdlinknet_dscatalyst_h3_lre1e-03_lrd1e-02_ep80_bs16_ds50_100_dr0p1_crxent1p0_softiou0p5'
# The epoch number to be loaded here
LOAD_EPOCH = 80
DATA_DIR = os.path.join(general_folder, data_specific_folder)  # Parent directory of input images in .jpg format
SAVE_ROOT = os.path.join(DATA_DIR, 'save_root/') # Parent directory of input images in .jpg format
FILE_LIST = os.path.join(DATA_DIR, 'file_list_raw.txt') # A list of full path of images to be tested on in DATA_DIR
DS_NAME = 'Catalyst' # Whatever you like to name it


PATCH_SIZE = (512, 512)


def calculate_intersection(image_1: np.ndarray, image_2: np.ndarray):
    return np.sum(np.multiply(image_1, image_2))


def calculate_union(image_1: np.ndarray, image_2: np.ndarray):
    return np.sum(np.count_nonzero(image_1 + image_2))


def calculate_iou(image_1: np.ndarray, image_2: np.ndarray):
    intersection = calculate_intersection(image_1, image_2)
    union = calculate_union(image_1, image_2)
    if union == 0:
        return 0
    else:
        return intersection / union


def plain_post_proc(conf, min_conf, min_area):
    tmp = conf > min_conf
    label = measure.label(tmp)
    props = measure.regionprops(label, conf)
    
    dummy = np.zeros(conf.shape)
    for p in props:
        if p.area > min_area:
            for x, y in p.coords:
                dummy[x, y] = 1
    return dummy


def tile_wise_validation(gt_dir, conf_dir, tile_list, min_conf=0.5, min_area=10, 
    progress_bar=False, gt_max=255, conf_max=255, 
    gt_postfix='_GT.png', conf_postfix='_RGB_conf.png', return_arrays=False):

    all_tiles_intersection, all_tiles_union = 0, 0
    tile_iou_dict = {}
    tile_processed_dict = {}
    gt_dict, conf_dict = {}, {}

    tile_iterable = tqdm(tile_list) if progress_bar else tile_list
    for tile_name in tile_iterable:
        gt_dict[tile_name] = io.imread(os.path.join(gt_dir, tile_name+gt_postfix)) / gt_max
        conf_dict[tile_name] = io.imread(os.path.join(conf_dir, tile_name+conf_postfix)) / conf_max
        processed = plain_post_proc(conf_dict[tile_name], min_conf, min_area)
        tile_processed_dict[tile_name] = processed
        
        all_tiles_intersection += calculate_intersection(gt_dict[tile_name], processed)
        all_tiles_union += calculate_union(gt_dict[tile_name], processed)
        tile_iou_dict[tile_name] = calculate_iou(gt_dict[tile_name], processed)

    all_tiles_iou = all_tiles_intersection / all_tiles_union if all_tiles_union != 0 else 0

    if return_arrays:
        return all_tiles_iou, gt_dict, conf_dict, tile_processed_dict
    else:
        return all_tiles_iou


def infer_confidence_map(DATA_DIR=DATA_DIR, SAVE_ROOT=SAVE_ROOT, FILE_LIST=FILE_LIST,
                        DS_NAME=DS_NAME, MODEL_DIR=MODEL_DIR ,LOAD_EPOCH=LOAD_EPOCH, extra_save_name=None ):
    """
    Extra save name is for changing the output name of the 
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', type=int, default=GPU)
    parser.add_argument('--model_dir', type=str, default=MODEL_DIR)
    parser.add_argument('--data_dir', type=str, default=DATA_DIR)
    parser.add_argument('--save_root', type=str, default=SAVE_ROOT)
    parser.add_argument('--load_epoch', type=int, default=LOAD_EPOCH)
    parser.add_argument('--ds_name', type=str, default=DS_NAME)
    parser.add_argument('--patch_size', type=str, default=PATCH_SIZE)
    parser.add_argument('--file_list', type=str, default=FILE_LIST)
    parser.add_argument('--compute_iou', dest='iou_eval', action='store_true')
    parser.add_argument('--no_compute_iou', dest='iou_eval', action='store_false')
    parser.set_defaults(iou_eval=False)
    super_args = parser.parse_args()

    # device, _ = misc_utils.set_gpu(super_args.gpu)
    device = 'cuda:{}'.format(super_args.gpu)


    def load_func_ct_tiles(data_dir, file_list=super_args.file_list, class_names=['panel', ]):
        if file_list:
            if not os.path.isfile(file_list):
                make_file_list(data_dir)
            with open(file_list, 'r') as f:
                rgb_files = f.read().splitlines()
        else:
            from glob import glob
            rgb_files = natsorted(glob(os.path.join(data_dir, '*.jpg')))
        lbl_files = [None] * len(rgb_files)
        assert len(rgb_files) == len(lbl_files)
        return rgb_files, lbl_files

    # # make file list for this
    # if not os.path.exists(FILE_LIST):
    #     make_file_list(DATA_DIR, 'png')
    
    # make file list for this
    if not os.path.exists(FILE_LIST):
        make_file_list(DATA_DIR)

    # init model
    args = network_io.load_config(super_args.model_dir)
    model = network_io.create_model(args)
    if LOAD_EPOCH:
        args['trainer']['epochs'] = super_args.load_epoch
    ckpt_dir = os.path.join(
        super_args.model_dir, 'epoch-{}.pth.tar'.format(args['trainer']['epochs']))
    network_utils.load(model, ckpt_dir)
    print('Loaded from {}'.format(ckpt_dir))
    model.to(device)
    model.eval()

    # eval on dataset
    if os.path.exists(os.path.join('/home/wh145/mrs/data/stats/custom', '{}.npy'.format(DS_NAME))):
        mean, std = np.load(os.path.join(
            '/home/wh145/mrs/data/stats/custom', '{}.npy'.format(DS_NAME)))
        print('Use {} mean and std stats: {}'.format(DS_NAME, (mean, std)))
    else:
        mean = (0.485, 0.456, 0.406)
        std = (0.229, 0.224, 0.225)
        print('Use default (imagenet) mean and std stats: {}'.format((mean, std)))

    tsfm_valid = A.Compose([
        A.Normalize(mean=mean, std=std),
        ToTensorV2(),
    ])
    if extra_save_name is None:
        save_dir = os.path.join(super_args.save_root, os.path.basename(
            network_utils.unique_model_name(args)))
    else:
        save_dir = os.path.join(super_args.save_root, extra_save_name)
    evaluator = eval_utils.Evaluator(
        super_args.ds_name, super_args.data_dir, tsfm_valid, device, load_func=load_func_ct_tiles)
    # evaluator.evaluate(model, PATCHS_SIZE, 2*model.lbl_margin,
    #                    pred_dir=save_dir, report_dir=save_dir)
    evaluator.infer(model=model, patch_size=PATCH_SIZE, overlap=2*model.lbl_margin,
                    pred_dir=save_dir, save_conf=True)

    if super_args.iou_eval:
        # calculate tile-wise IOU
        with open(super_args.file_list, 'r') as fp:
            tile_list = [os.path.basename(s).split('.')[0] for s in fp.readlines()]

        print(
            tile_wise_validation(
                super_args.data_dir, save_dir, tile_list, min_conf=0.5, min_area=0,
                gt_max=1, conf_max=255, gt_postfix='.png', conf_postfix='_conf.png'
            )
        )

if __name__ == '__main__':
    # # The individual evaluation
    infer_confidence_map()