from __future__ import division
import _init_paths

import os.path
import numpy as np
import gzip
import cPickle as pickle
import skimage.morphology
import time
import re
import sys

from params import unet_params as P
from utils import output_size_for_input
from augment import augment
import normalize
import loss_weighting

NET_DEPTH = P.DEPTH # Default 5
INPUT_SIZE = P.INPUT_SIZE # Default 512
OUTPUT_SIZE = output_size_for_input(INPUT_SIZE, NET_DEPTH)

_EPSILON = 1e-8

def get_image(filename, deterministic):
    # print filename
    with gzip.open(filename,'rb') as f:
        lung = pickle.load(f)

    np.set_printoptions(threshold = 'nan')

    segmentation_filename = filename.replace('lung', 'lung_masks')
    truth_filename = filename.replace('lung', 'nodule')
    if os.path.isfile(truth_filename):
        with gzip.open(truth_filename, 'rb') as f:
            truth = np.array(pickle.load(f), dtype = np.float32)
    else:
        truth = np.zeros_like(lung)

    # print np.sum(truth)
    # print truth.shape
    # print truth_filename

    if os.path.isfile(segmentation_filename):
        with gzip.open(segmentation_filename, 'rb') as f:
            # print "segment file: ", segmentation_filename
            outside = np.where(pickle.load(f) > 0, 0, 1)
    else:
        outside = np.where(lung == 0, 1, 0)
        # print outside
        print 'lung masks are not found'
    # print "[1] truth number and outside number: ", np.sum(truth == 1), np.sum(outside)

    if P.ERODE_SEGMENTATION > 0:
        kernel = skimage.morphology.disk(P.ERODE_SEGMENTATION)
        outside = skimage.morphology.binary_erosion(outside, kernel)

    outside = np.array(outside, dtype = np.float32)

    if P.AUGMENT and not deterministic:
        lung, truth, outside = augment([lung, truth, outside])

    if P.RANDOM_CROP > 0:
        im_x = lung.shape[0]
        im_y = lung.shape[1]
        x = np.random.randint(0, max(1, im_x - P.RANDOM_CROP))
        y = np.random.randint(0, max(1, im_y - P.RANDOM_CROP))

        lung = lung[x : x + P.RANDOM_CROP, y : y + P.RANDOM_CROP]
        truth = truth[x : x + P.RANDOM_CROP, y : y + P.RANDOM_CROP]
        outside = outside[x : x + P.RANDOM_CROP, y : y + P.RANDOM_CROP]

    truth = np.array(np.round(truth), dtype = np.int64)
    outside = np.array(np.round(outside), dtype = np.int64)
    # print "[2] truth number and outside number: ", np.sum(truth == 1), np.sum(outside)
    
    # Set label of outside pixels to -10
    truth = truth - (outside * 10)

    lung = lung * (1 - outside)
    lung = lung - outside * 3000

    if P.INPUT_SIZE > 0:
        lung = crop_or_pad(lung, INPUT_SIZE, -3000)
        truth = crop_or_pad(truth, OUTPUT_SIZE, 0)
        outside = crop_or_pad(outside, OUTPUT_SIZE, 1)
    else:
        out_size = output_size_for_input(lung.shape[1], P.DEPTH)
        # lung = crop_or_pad(lung, INPUT_SIZE, -1000)
        truth = crop_or_pad(truth, out_size, 0)
        outside = crop_or_pad(outside, out_size, 1)

    lung = normalize.normalize(lung)
    lung = np.expand_dims(np.expand_dims(lung, axis = 0), axis = 0)

    if P.ZERO_CENTER:
        lung = lung - P.MEAN_PIXEL

    if P.GAUSSIAN_NOISE > 0:
        sigma = P.GAUSSIAN_NOISE
        mean = 0.0
        gauss = np.random.normal(mean, sigma, lung.shape)
        lung = lung + gauss

    truth = np.array(np.expand_dims(np.expand_dims(truth, axis = 0), axis = 0), dtype = np.int64)

    # print "[3] truth number and outside number: ", np.sum(truth == 1), np.sum(outside)
    return lung, truth

def crop_or_pad(image, desired_size, pad_value):
    if image.shape[0] < desired_size:
        offset = int(np.ceil((desired_size - image.shape[0]) / 2))
        image = np.pad(image, offset, 'constant', constant_values = pad_value)

    if image.shape[0] > desired_size:
        offset = (image.shape[0] - desired_size) // 2
        image = image[offset : offset + desired_size, offset : offset + desired_size]

    return image

def load_images(filenames, deterministic = False):
    slices = [get_image(filename, deterministic) for filename in filenames]
    lungs, truths = zip(*slices)

    l = np.array(np.concatenate(lungs, axis = 0), dtype = np.float32)
    t = np.concatenate(truths, axis = 0)

    # Weight the loss by class balancing, classes other than 0 and 1
    # get set to 0 (the background is -10)
    w = loss_weighting.weight_by_class_balance(t, classes = [0, 1])
    # print w

    # Set -1 labels back to label 0
    t = np.clip(t, 0, 100000)

    return l, t, w, filenames

def get_scan_name(filename):
    scan_name = filename.replace('\\', '/').split('/')[-1].split('_')[0]
    return scan_name

def train_splits_by_z(filenames, data_resolution = 0.5, n_splits = None):
    import pandas as pd

    cur_dir = os.path.dirname(os.path.abspath(__file__))
    zspacing_file = os.path.join(cur_dir, "./../../../data/imagename_zspacing.csv")
    resolution_of_scan = pd.read_csv(zspacing_file, header = None, names = ['filename', 'spacing'], index_col = False)

    scan_names = set(map(get_scan_name, filenames))
    resolutions = [resolution_of_scan[resolution_of_scan['filename'] == scan].iloc[0]['spacing'] for scan in scan_names]
    scan_filenames = []
    for scan in scan_names:
        scan_filenames.append(filter(lambda x: scan in x, filenames))
    # print len(scan_filenames)

    split_per_scan = [int(np.round(r / data_resolution)) for r in resolutions] # Amount of splits to divide the filenames over
    random_offsets = [np.random.permutation(range(x)) for x in split_per_scan]

    if n_splits is None:
        n_splits = np.round(max(resolutions) / data_resolution)

    splits = [[] for _ in xrange(n_splits)]

    for i, s in enumerate(splits):
        for r, scan, filenames_in_scan, n, offset in zip(resolutions, scan_names, scan_filenames, split_per_scan, random_offsets):
            # n = int(np.round(r / data_resolution))
            start = offset[i % n]
            s += filenames_in_scan[start % n::n]

    return splits
