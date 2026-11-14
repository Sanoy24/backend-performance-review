

import timeit
import statistics

import numpy as np

N = 4000

def setup():
    global arrays, names
    arrays = [
        np.arange(5)
        for i in range(N)
    ]

    names = [
        'field_{}'.format(i)
        for i in range(N)
    ]
 
def workload():
    global arrays, names
    arr = np.core.records.fromarrays(arrays, names=names)

runtimes = timeit.repeat(workload, number=30, repeat=100, setup=setup)

# Print runtime mean and std deviation.
print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))
