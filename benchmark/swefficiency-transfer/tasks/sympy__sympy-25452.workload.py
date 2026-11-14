import timeit
import statistics
from sympy import symbols, Matrix

from sympy.core.cache import clear_cache

def setup():
    global Z

    clear_cache()
    a, b = symbols('a b')
    Z = Matrix([[a, b],
            [b, a + b]])

def workload():
    global Z
    _ = Z.inv()
    
runtimes = timeit.repeat(workload, number=1, repeat=100, setup=setup)

print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))