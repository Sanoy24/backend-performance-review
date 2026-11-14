import timeit
import statistics

import cftime
import numpy as np
import pandas as pd
import xarray as xr

def setup(): 
    global ds
    lat = np.linspace(-90, 90, 180)
    lon = np.linspace(-180, 180, 360)
    time = pd.date_range("1850-01-01", periods=10000, freq="D")

    # Generate random data
    data = np.random.rand(len(time), len(lat), len(lon))

    # Create dataset
    ds = xr.Dataset(
        {"random_data": (["time", "lat", "lon"], data)},
        coords={"lat": lat, "lon": lon, "time": time},
    )

    labeled_time = []
    for coord in ds.time:
        labeled_time.append(cftime.datetime(coord.dt.year, coord.dt.month, 1))

    ds.coords["year_month"] = xr.DataArray(data=labeled_time, dims="time")
    

def workload():
    global ds
    ds.groupby("time.month")

runtimes = timeit.repeat(workload, number=1, repeat=25, setup=setup)

print("Mean:", statistics.mean(runtimes))
print("Std Dev:", statistics.stdev(runtimes))
