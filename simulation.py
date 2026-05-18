"""
Two-lane Nagel-Schreckenberg traffic simulation with an on-ramp.

The model is a discrete cellular automaton: roads are sequences of cells,
each holding either nothing or a single vehicle. Every step has three phases:
  1. lane changes
  2. forward motion (accelerate -> brake -> randomize -> move)
  3. spawn new vehicles at the open boundary
"""

import random
import uuid

import config


class TrafficSimulation:
    """
    Three lanes represented as lists of cells:
      - lane 0: left (main)
      - lane 1: right (main)
      - lane 2: on-ramp (shorter, ends before the road end)

    Each cell is either None or a car dict:
      {'id': str, 'v': int, 'crashed': bool (optional)}.
    """

    # Lane indices
    LANE_LEFT = 0
    LANE_RIGHT = 1
    LANE_RAMP = 2

    def __init__(self, L=None, density=None, v_max=None, p=None):
        self.L = L if L is not None else config.ROAD_LENGTH
        self.density = density if density is not None else config.INITIAL_DENSITY
        self.v_max = v_max if v_max is not None else config.INITIAL_V_MAX
        self.p = p if p is not None else config.INITIAL_P

        # Three lanes of equal length L (though the ramp is shorter, we keep the same list length and just ignore the tail)
        self.lanes = [[None] * self.L for _ in range(3)]

        # Counters used to give each spawned car a unique id
        self.sim_id = uuid.uuid4().hex[:6]
        self.car_id_counter = 0

    # ---------- Helpers: gap calculations

    def _lane_limit(self, lane):
        """Usable length of a lane (ramp is shorter than main lanes)."""
        return config.RAMP_LENGTH if lane == self.LANE_RAMP else self.L

    def _gap_ahead(self, lane, x):
        """Number of empty cells in front of a car at position x."""
        limit = self._lane_limit(lane)
        for i in range(1, self.v_max + 2):
            # Approaching the end of the lane
            if x + i >= limit:
                # Ramp cars must stop before the end; main-lane cars exit the scene
                return (limit - x - 1) if lane == self.LANE_RAMP else self.v_max
            # Hit another car ahead
            if self.lanes[lane][x + i] is not None:
                return i - 1
        return self.v_max

    def _gap_behind(self, lane, x):
        """Number of empty cells behind position x (used when changing lanes)."""
        for i in range(1, self.v_max + 2):
            if x - i < 0:
                return self.v_max
            if self.lanes[lane][x - i] is not None:
                return i - 1
        return self.v_max

    # ---------- Single simulation step

    def step(self):
        """
        Execute one NaSch step. Returns:
          (flow, lane_changes, density, avg_speed)
        """
        lane_changes = self._phase_lane_changes()
        flow = self._phase_move()
        self._phase_spawn()
        density, avg_speed = self._compute_metrics()
        return flow, lane_changes, density, avg_speed

    # Phase 1: lane changes

    def _phase_lane_changes(self):
        """Each car decides whether to change lane; we apply changes in one batch."""
        moves = []  # (source_lane, target_lane, x)

        for lane in range(3):
            for x in range(self.L):
                car = self.lanes[lane][x]
                if not car or car.get('crashed'):
                    continue

                target = self._target_lane(lane)
                if self._can_change_lane(car, lane, target, x):
                    moves.append((lane, target, x))

        # Apply changes on a fresh copy so cars don't interfere with each other
        new_lanes = [list(row) for row in self.lanes]
        for src, dst, x in moves:
            if new_lanes[dst][x] is None:
                new_lanes[dst][x] = new_lanes[src][x]
                new_lanes[src][x] = None

        self.lanes = new_lanes
        return len(moves)

    def _target_lane(self, lane):
        """Which lane this car would move to."""
        if lane == self.LANE_RAMP:
            return self.LANE_RIGHT       # ramp always merges into the right lane
        return 1 - lane                  # left <-> right

    def _can_change_lane(self, car, lane, target, x):
        """True when changing lane is both safe and worthwhile."""
        # Safety: target cell empty and nobody is closing in from behind
        cell_free = self.lanes[target][x] is None
        safe_behind = self._gap_behind(target, x) >= self.v_max
        if not (cell_free and safe_behind):
            return False

        # Ramp cars merge as soon as it is safe
        if lane == self.LANE_RAMP:
            return True

        # Main-lane cars only switch when crowded and the neighbour is roomier
        gap_here = self._gap_ahead(lane, x)
        gap_there = self._gap_ahead(target, x)
        return gap_here < car['v'] and gap_there > gap_here

    # Phase 2: forward motion

    def _phase_move(self):
        """Standard NaSch update: accelerate, brake to gap, randomize, move."""
        new_lanes = [[None] * self.L for _ in range(3)]
        flow = 0  # cars that exited the scene this step

        for lane in range(3):
            for x in range(self.L):
                car = self.lanes[lane][x]
                if car is None:
                    continue

                # Crashed cars stay put and block the lane
                if car.get('crashed'):
                    new_lanes[lane][x] = car
                    continue

                # 1) accelerate, but never above v_max or available gap
                # 2) random brake with probability p
                v = min(car['v'] + 1, self.v_max, self._gap_ahead(lane, x))
                if random.random() < self.p:
                    v = max(v - 1, 0)
                car['v'] = v

                # 3) move
                new_x = x + v
                limit = self._lane_limit(lane)

                if lane != self.LANE_RAMP and new_x >= self.L:
                    flow += 1                              # car exited the scene
                elif lane == self.LANE_RAMP and new_x >= limit:
                    new_lanes[lane][limit - 1] = car       # forced stop at ramp end
                else:
                    new_lanes[lane][new_x] = car

        self.lanes = new_lanes
        return flow

    # Phase 3: spawn

    def _phase_spawn(self):
        """Inject new cars at the open boundary."""
        # Main lanes: cell 0 with probability `density`
        for lane in (self.LANE_LEFT, self.LANE_RIGHT):
            if self.lanes[lane][0] is None and random.random() < self.density:
                self.lanes[lane][0] = self._make_car(self.v_max)

        # Ramp: spawn less often (×0.6) and at a lower starting speed
        ramp_start = config.RAMP_START
        if (self.lanes[self.LANE_RAMP][ramp_start] is None
                and random.random() < self.density * 0.6):
            self.lanes[self.LANE_RAMP][ramp_start] = self._make_car(max(0, self.v_max - 2))

    def _make_car(self, v):
        """Construct a new car dict with a unique id."""
        car = {'id': f"{self.sim_id}_{self.car_id_counter}", 'v': v}
        self.car_id_counter += 1
        return car

    # --- Statistics

    def _compute_metrics(self):
        """Density on main lanes and mean speed across all lanes."""
        cars_main = sum(1 for lane in (self.LANE_LEFT, self.LANE_RIGHT)
                        for c in self.lanes[lane] if c)
        density = cars_main / (self.L * 2)

        all_cars = [c for lane in self.lanes for c in lane if c]
        avg_speed = sum(c['v'] for c in all_cars) / len(all_cars) if all_cars else 0

        return density, avg_speed
