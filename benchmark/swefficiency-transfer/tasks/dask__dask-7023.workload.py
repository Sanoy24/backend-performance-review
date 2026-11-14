import timeit
import statistics

import numpy as np
import dask.array as da

N_rows = 10**4
N_cols = 10**4
chunks = (1000, 1000)
new_chunks = ((20,) * 50, (10,) * 100)

# Global variable for the array so setup can define it and workload can reuse it
array = None

def setup():
    global array
    array = da.random.random((N_rows, N_cols), chunks=chunks)
    array._chunks = new_chunks
    _ = array.shape

def workload():
    _ = array.shape

runtimes = timeit.repeat(workload, number=1, repeat=1000, setup=setup)

# Print runtime mean and std deviation.
print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))
