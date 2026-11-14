import timeit
import statistics
import numpy as np
from astropy.coordinates import SkyCoord, UnitSphericalRepresentation
import astropy.units as u

class SkyCoordBenchmarks:
    def setup(self):
        self.coord_scalar = SkyCoord(1, 2, unit="deg", frame="icrs")
        lon, lat = np.ones((2, 1000))
        self.coord_array_1e3 = SkyCoord(lon, lat, unit="deg", frame="icrs")
        self.lon_1e6, self.lat_1e6 = np.ones((2, int(1e6)))
        self.coord_array_1e6 = SkyCoord(
            self.lon_1e6, self.lat_1e6, unit="deg", frame="icrs"
        )
        
        self.scalar_q_ra = 1 * u.deg
        self.scalar_q_dec = 2 * u.deg
        
        np.random.seed(12345)
        self.array_q_ra = np.random.rand(int(1e6)) * 360 * u.deg
        self.array_q_dec = (np.random.rand(int(1e6)) * 180 - 90) * u.deg
        
        self.scalar_repr = UnitSphericalRepresentation(
            lon=self.scalar_q_ra, lat=self.scalar_q_dec
        )
        self.array_repr = UnitSphericalRepresentation(
            lon=self.array_q_ra, lat=self.array_q_dec
        )

    def time_init_repr_array_noframe(self):
        SkyCoord(self.array_repr)

def setup():
    global bench
    bench = SkyCoordBenchmarks()
    bench.setup()
    print("finishone")

def workload():
    global bench
    bench.time_init_repr_array_noframe()

runtimes = timeit.repeat(workload, number=1, repeat=200, setup=setup)

print("Mean:", statistics.mean(runtimes[-100:]))
print("Std Dev:", statistics.stdev(runtimes[-100:]))