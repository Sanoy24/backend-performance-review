import timeit
import statistics

import pandas as pd

dates = pd.date_range('1900', '2000').tz_localize('+01:00').strftime('%Y-%d-%m %H:%M:%S%z').tolist()
dates.append('2020-01-01 00:00:00+02:00')

def workload():
    pd.to_datetime(dates, format='%Y-%d-%m %H:%M:%S%z')
    
runtimes = timeit.repeat(workload, number=1, repeat=25)

# Print runtime mean and std deviation.
print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))
