"""
Centralized configuration for the traffic simulation.

All tunable values live here so it is easy to adjust the simulation
without hunting through multiple files.
"""

# ----- Road geometry -----
ROAD_LENGTH = 150        # number of cells on main lanes
RAMP_START = 70          # cell where on-ramp begins
RAMP_LENGTH = 115        # last usable cell on the on-ramp

# ----- Default simulation parameters -----
INITIAL_DENSITY = 0.1    # spawn probability per step (also "inflow rate")
INITIAL_V_MAX = 2        # max vehicle speed (cells per step)
INITIAL_P = 0.3          # random deceleration probability ("dawdling")
INITIAL_SPEED_MULTIPLIER = 1.0  # how fast the simulation runs vs real time

# ----- Worker timing -----
TICK_INTERVAL = 0.15     # base seconds between simulation steps

# ----- Statistics -----
HISTORY_WINDOW = 30      # how many recent samples to keep for averages

# ----- Special events -----
ACCIDENT_LANE = 1        # right main lane
ACCIDENT_CELL = 65       # where the accident is placed

# ----- HTTP server -----
DEFAULT_PORT = 8080
HOST = '0.0.0.0'
