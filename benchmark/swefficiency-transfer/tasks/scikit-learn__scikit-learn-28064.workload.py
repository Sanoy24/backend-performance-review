import timeit
import statistics

import numpy as np

from sklearn.datasets import make_classification
from sklearn.ensemble._hist_gradient_boosting.binning import _BinMapper

n_samples, n_features = 200_000, 20
n_bins = 256

X, y = make_classification(n_samples=n_samples, n_features=n_features)
categorical_remapped = np.zeros(n_features, dtype=bool)

def setup():
    global bin_mapper, bin_mapper_multithread
    bin_mapper = _BinMapper(
        n_bins=n_bins,
        is_categorical=categorical_remapped,
        known_categories=None,
        random_state=1,
        n_threads=1,
    )
    bin_mapper_multithread = _BinMapper(
        n_bins=n_bins,
        is_categorical=categorical_remapped,
        known_categories=None,
        random_state=1,
        n_threads=4,
    )

def workload():
    bin_mapper.fit(X) 
    bin_mapper.fit_transform(X)  
    bin_mapper_multithread.fit(X)  
    bin_mapper_multithread.fit_transform(X)
    
runtimes = timeit.repeat(workload, number=1, repeat=10, setup=setup)

print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))
