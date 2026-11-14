import timeit
import statistics

import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from io import BytesIO

from matplotlib.figure import Figure
import numpy as np

def setup():
    global stream, fig_many
    # Random Number Generator.
    rng = np.random.default_rng()
    
    # Constants.
    figsize = (10, 6)
    ncols = 3
    nrows = 10
    size = 100
    size_total = ncols * nrows * size
    
    # Figure with many subplots.
    fig_many = Figure(figsize=figsize)
    axs_many = fig_many.subplots(ncols=ncols, nrows=nrows)
    
    # Figure with many subplots and sharex='col'.
    fig_many_sharex = Figure(figsize=figsize)
    axs_many_sharex = fig_many_sharex.subplots(ncols=ncols, nrows=nrows, sharex='col')
    
    # Figure with a single axes.
    fig_single = Figure(figsize=figsize)
    ax_single = fig_single.subplots()
    
    # Helper-function: Generate random line-plots in the many subplots.
    def generate_fig_many(axs):
        for row in range(nrows):
            for col in range(ncols):
                ax = axs[row, col]
                x = rng.normal(loc=row+1, scale=col+1, size=size)
                y = rng.normal(loc=col+1, scale=row+1, size=size)
                x = np.sort(x)
                ax.plot(x, y);
                ax.set_yticks([])
    
    # Generate fig_many 
    generate_fig_many(axs=axs_many)
    fig_many.tight_layout()
    
    # Generate fig_many_sharex
    generate_fig_many(axs=axs_many_sharex)
    fig_many_sharex.tight_layout()
    
    # Generate fig_single
    x = rng.normal(size=size_total)
    y = rng.normal(size=size_total)
    x = np.sort(x)
    ax_single.plot(x, y);
    fig_single.tight_layout()


def workload():
    global stream, fig_many
    stream = BytesIO()
    fig_many.savefig(stream, format='svg', bbox_inches='tight')
    s = stream.getvalue()

runtimes = timeit.repeat(workload, number=1, repeat=25, setup=setup)

print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))