import gzip
import cPickle as pickle
import numpy as np

import _init_paths
from normalize import normalize
from params import wrn_params as P
import augment

def load_images(image_paths, deterministic = False):
    X = []

    labels = []

    for image_path in image_paths:
        with gzip.open(image_path) as f:
            xy_xz_yz = pickle.load(f)
            x, y, z = xy_xz_yz
            if P.AUGMENT and not deterministic:
                x, y, z = augment.augment(xy_xz_yz)

            offset = (len(x) - P.INPUT_SIZE) / 2

            if offset > 0:
                x = x[offset : -offset, offset : -offset]
                y = y[offset : -offset, offset : -offset]
                z = z[offset : -offset, offset : -offset]

            X.append([x])
            X.append([y])
            X.append([z])

        # label = [0,1] if "True" in image_path else [1,0]
        label = [1] if "True" in image_path else [0]
        labels.append(label)
        labels.append(label)
        labels.append(label)

    X = np.array(X)
    X = normalize(X)

    if P.ZERO_CENTER:
        X -= P.MEAN_PIXEL

    return np.array(X, dtype = np.float32), np.array(labels)
