"""
Traffic Simulation: Two-Lane Nagel-Schreckenberg Model
Author: Senior Data Scientist
"""

import json
import random
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time
import uuid
from typing import List, Dict, Optional, Tuple, Any

class TrafficSimulation:
    def __init__(self, L: int = 100, density: float = 0.2, v_max: int = 5, p: float = 0.3):
        self.L: int = L                  
        self.density: float = density      # In an open system, this serves as the Inflow Rate Probability
        self.v_max: int = v_max          
        self.p: float = p                  # Probability of random braking
        
        # Initialization of three lanes. 0: Left, 1: Right, 2: On-Ramp
        self.lanes: List[List[Optional[Dict[str, Any]]]] = [[None for _ in range(L)] for _ in range(3)]
        
        self.sim_id: str = str(uuid.uuid4())[:8]
        self.car_id_counter: int = 0

    def _get_gap(self, lane: int, x: int) -> int:
        """Returns the distance in free cells preserving physical road end constraints (open boundaries)."""
        limit = self.L if lane < 2 else 115 
        for i in range(1, self.v_max + 2):
            if x + i >= limit:
                # Car reaching end of ramp stops if no gap vs main lane cars driving off-screen safely.
                return (limit - x - 1) if lane == 2 else self.v_max
            if self.lanes[lane][x + i] is not None:
                return i - 1
        return self.v_max

    def _get_back_gap(self, lane: int, x: int) -> int:
        """Checks the gap behind for safety from an open geolocation perspective."""
        for i in range(1, self.v_max + 2):
            if x - i < 0:
                return self.v_max
            if self.lanes[lane][x - i] is not None:
                return i - 1
        return self.v_max

    def step(self) -> Tuple[int, int, float, float]:
        """
        A single timestep of the NaSch model for an OPEN route ("Straight Section with On-Ramp Hub").
        Returns: (total_flow, lane_changes, actual_density, avg_speed)
        """
        # ==================== PHASE 1: Lane Changes ====================
        new_lanes = [list(self.lanes[lane]) for lane in range(3)]
        moves: List[Dict[str, int]] = []
        
        for lane in range(3):
            for x in range(self.L):
                car = self.lanes[lane][x]
                if car and not car.get('crashed', False):
                    v = car['v']
                    
                    if lane == 2:
                        other_lane = 1
                        gap_back_other = self._get_back_gap(other_lane, x)
                        safety = (self.lanes[other_lane][x] is None) and (gap_back_other >= self.v_max)
                        if safety:
                            moves.append({'from': lane, 'to': other_lane, 'x': x})
                    else:
                        other_lane = 1 - lane
                        gap_current = self._get_gap(lane, x)
                        gap_other = self._get_gap(other_lane, x)
                        gap_back_other = self._get_back_gap(other_lane, x)
                        
                        incentive = (gap_current < v) and (gap_other > gap_current)
                        safety = (self.lanes[other_lane][x] is None) and (gap_back_other >= self.v_max)
                        
                        max_merge_zone = 100
                        if lane == 2 and not ((x >= max_merge_zone) or gap_current < v):
                            continue
                                
                        if incentive and safety:
                            moves.append({'from': lane, 'to': other_lane, 'x': x})
        
        # Execute lateral transfers
        for move in moves:
            l_from, l_to, x = move['from'], move['to'], move['x']
            if new_lanes[l_to][x] is None:
                new_lanes[l_to][x] = new_lanes[l_from][x]
                new_lanes[l_from][x] = None

        self.lanes = new_lanes
        
        # ==================== PHASE 2: Forward Motion and Spawn Generation ====================
        new_lanes = [[None for _ in range(self.L)] for _ in range(3)]
        total_flow = 0 
        
        for lane in range(3):
            for x in range(self.L):
                car = self.lanes[lane][x]
                if car is not None:
                    if car.get('crashed', False):
                        new_lanes[lane][x] = car
                        continue
                        
                    v = car['v']
                    gap = self._get_gap(lane, x)
                    
                    v = min(v + 1, self.v_max, gap)             
                    if random.random() < self.p: 
                        v = max(v - 1, 0)
                        
                    car['v'] = v
                    new_x = x + v 
                    
                    if lane < 2 and new_x >= self.L: 
                        total_flow += 1 
                    elif lane == 2 and new_x >= 115:
                        new_lanes[lane][114] = car 
                    else:
                        new_lanes[lane][new_x] = car
                        
        # ==================== PHASE 3: Inflow Injection ====================
        for lane in range(2):
            if new_lanes[lane][0] is None and random.random() < self.density:
                new_lanes[lane][0] = {'id': f"{self.sim_id}_{self.car_id_counter}", 'v': self.v_max}
                self.car_id_counter += 1
                
        ramp_start = 70 
        if new_lanes[2][ramp_start] is None and random.random() < (self.density * 0.6):
            new_lanes[2][ramp_start] = {'id': f"{self.sim_id}_{self.car_id_counter}", 'v': max(0, self.v_max - 2)} 
            self.car_id_counter += 1
            
        self.lanes = new_lanes
        
        # Calculate macroscopic traffic observables
        actual_cars_main = sum(1 for lane in range(2) for c in self.lanes[lane] if c)
        actual_density = actual_cars_main / (self.L * 2)
        
        actual_cars_total = actual_cars_main + sum(1 for c in self.lanes[2] if c)
        total_v = sum(c['v'] for lane in self.lanes for c in lane if c)
        avg_speed = total_v / actual_cars_total if actual_cars_total > 0 else 0

        return total_flow, len(moves), actual_density, avg_speed


# ====================================================================
# SERVER STATE AND WORKER
# ====================================================================

class ServerState:
    def __init__(self):
        self.sim_params: Dict[str, Any] = {'density': 0.1, 'p': 0.3, 'v_max': 2, 'speed_multiplier': 1.0}
        self.is_running: bool = False
        
        self.flow_history: List[int] = []
        self.density_history: List[float] = []
        self.speed_history: List[float] = []
        
        self.total_lane_changes: int = 0
        self.total_sim_ticks: int = 0
        self.live_sim: TrafficSimulation = self._create_sim()
        
    def _create_sim(self) -> TrafficSimulation:
        return TrafficSimulation(
            L=150, 
            density=self.sim_params['density'], 
            p=self.sim_params['p'], 
            v_max=self.sim_params['v_max']
        )

    def reset_simulation(self):
        self.live_sim = self._create_sim()
        self.flow_history.clear()
        self.density_history.clear()
        self.speed_history.clear()
        self.total_lane_changes = 0
        self.total_sim_ticks = 0

state = ServerState()

def sim_worker():
    """Background daemon simulating vehicular movement independent of HTTP lifecycle."""
    while True:
        if state.is_running:
            f, lc, d, s = state.live_sim.step()
            state.total_sim_ticks += 1
            state.flow_history.append(f)
            state.density_history.append(d)
            state.speed_history.append(s)
            state.total_lane_changes += lc
            
            # Maintain sliding window of metrics
            if len(state.flow_history) > 30: 
                state.flow_history.pop(0)
                state.density_history.pop(0)
                state.speed_history.pop(0)
                
        sleep_time = 0.15 / state.sim_params['speed_multiplier']
        time.sleep(sleep_time) 

class APIServer(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_HEAD(self):
        """Satisfies health checks from hosting platforms like Render by returning headers without a body."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == '/':
            try:
                with open('index.html', 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"index.html not found.")
            return

        if parsed.path == '/api/state':
            avg_f = sum(state.flow_history) / len(state.flow_history) if state.flow_history else 0
            avg_d = sum(state.density_history) / len(state.density_history) if state.density_history else 0
            avg_s = sum(state.speed_history) / len(state.speed_history) if state.speed_history else 0
            
            res = {
                'lanes': state.live_sim.lanes,
                'is_running': state.is_running,
                'stats': {
                    'density': avg_d, 
                    'flow': avg_f,
                    'lane_changes': state.total_lane_changes,
                    'speed': avg_s,
                    'ticks': len(state.flow_history),
                    'total_ticks': state.total_sim_ticks 
                }
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
            
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(res).encode('utf-8'))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == '/api/config':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            
            if 'density' in data and float(data['density']) != state.sim_params['density']: 
                state.sim_params['density'] = float(data['density'])
                state.live_sim.density = state.sim_params['density']
                
            if 'p' in data: 
                state.sim_params['p'] = float(data['p'])
                state.live_sim.p = state.sim_params['p']
                
            if 'v_max' in data: 
                state.sim_params['v_max'] = int(data['v_max'])
                state.live_sim.v_max = state.sim_params['v_max']
                
            if 'speed_multiplier' in data:
                state.sim_params['speed_multiplier'] = float(data['speed_multiplier'])
                
            if 'is_running' in data: 
                state.is_running = bool(data['is_running'])
            
            if data.get('trigger_accident'):
                state.live_sim.lanes[1][65] = {'id': 'CRASH_1', 'v': 0, 'crashed': True}
            
            if data.get('clear_accident'):
                if state.live_sim.lanes[1][65] and state.live_sim.lanes[1][65].get('crashed'):
                    state.live_sim.lanes[1][65] = None
            
            if data.get('reset'): 
                state.reset_simulation()
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

import os

if __name__ == "__main__":
    threading.Thread(target=sim_worker, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    server_address = ('0.0.0.0', port)
    server = HTTPServer(server_address, APIServer)
    
    print(f"Simulation engine securely bound to {server_address[0]}:{server_address[1]}")
    print("REST APIs active. Awaiting visualization client at index.html")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHalting simulation server...")
        server.server_close()
