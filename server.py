"""
Traffic Simulation: Two-Lane Nagel-Schreckenberg Model
Author: Wiktor Węgiel
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
    """
    Symulacja ruchu - model Nagela-Schreckenberga (NaSch) z otwartymi granicami.

    Mamy trzy pasy reprezentowane jako listy komórek:
      - pas 0: lewy (główny)
      - pas 1: prawy (główny)
      - pas 2: rozbiegowy (krótszy, kończy się przed końcem trasy)

    Każda komórka jest pusta (None) albo trzyma auto:
      {'id': str, 'v': int, 'crashed': bool (opcjonalnie)}.
    """

    # Indeksy pasów - czytelniej niż 0/1/2 w środku kodu
    LANE_LEFT = 0
    LANE_RIGHT = 1
    LANE_RAMP = 2

    # Geometria pasa rozbiegowego
    RAMP_LENGTH = 115   # użyteczna długość pasa rozbiegowego (komórki 0..114)
    RAMP_START = 70     # od tej komórki pojawiają się auta na rozbiegowym

    def __init__(self, L=100, density=0.2, v_max=5, p=0.3):
        self.L = L                  # długość pasów głównych (komórki)
        self.density = density      # prawdopodobieństwo wjazdu auta w danym kroku
        self.v_max = v_max          # maksymalna prędkość (komórki na krok)
        self.p = p                  # prawdopodobieństwo losowego zwolnienia

        # Trzy pasy o tej samej długości L (rozbiegowy używa tylko 0..RAMP_LENGTH-1)
        self.lanes = [[None] * L for _ in range(3)]

        # Liczniki do nadawania unikalnych ID nowo spawnowanym autom
        self.sim_id = uuid.uuid4().hex[:6]
        self.car_id_counter = 0

    # ---------- Pomocnicze: liczenie odstępów między autami ----------

    def _lane_limit(self, lane):
        """Zwraca długość użyteczną pasa (rozbiegowy jest krótszy niż główne)."""
        return self.RAMP_LENGTH if lane == self.LANE_RAMP else self.L

    def _gap_ahead(self, lane, x):
        """Ile pustych komórek jest przed autem na pozycji x na danym pasie."""
        limit = self._lane_limit(lane)
        for i in range(1, self.v_max + 2):
            # Zbliżamy się do końca pasa
            if x + i >= limit:
                # Na rozbiegowym musimy zatrzymać się przed końcem,
                # na pasach głównych zakładamy "otwartą drogę" (auta wyjeżdżają).
                return (limit - x - 1) if lane == self.LANE_RAMP else self.v_max
            # Trafiliśmy na inne auto przed sobą
            if self.lanes[lane][x + i] is not None:
                return i - 1
        return self.v_max

    def _gap_behind(self, lane, x):
        """Ile pustych komórek jest za pozycją x (potrzebne przy ocenie zmiany pasa)."""
        for i in range(1, self.v_max + 2):
            if x - i < 0:
                return self.v_max
            if self.lanes[lane][x - i] is not None:
                return i - 1
        return self.v_max

    # ---------- Pojedynczy krok symulacji (3 fazy) ----------

    def step(self):
        """
        Jeden krok modelu NaSch:
          Faza 1: zmiany pasa
          Faza 2: ruch do przodu (przyspieszenie -> hamowanie -> losowość -> przesunięcie)
          Faza 3: wjazd nowych aut (inflow)

        Zwraca: (przepływ, liczba_zmian_pasa, gęstość, średnia_prędkość)
        """
        lane_changes = self._phase_lane_changes()
        flow = self._phase_move()
        self._phase_spawn()
        density, avg_speed = self._compute_metrics()
        return flow, lane_changes, density, avg_speed

    # --- Faza 1 ---

    def _phase_lane_changes(self):
        """Każde auto sprawdza czy chce i może zmienić pas, potem zmiany robimy naraz."""
        moves = []  # lista (pas_zrodlowy, pas_docelowy, x)

        for lane in range(3):
            for x in range(self.L):
                car = self.lanes[lane][x]
                if not car or car.get('crashed'):
                    continue

                target = self._target_lane(lane)
                if self._can_change_lane(car, lane, target, x):
                    moves.append((lane, target, x))

        # Stosujemy zmiany na świeżej kopii, żeby auta sobie nie wchodziły w drogę
        new_lanes = [list(row) for row in self.lanes]
        for src, dst, x in moves:
            if new_lanes[dst][x] is None:
                new_lanes[dst][x] = new_lanes[src][x]
                new_lanes[src][x] = None

        self.lanes = new_lanes
        return len(moves)

    def _target_lane(self, lane):
        """Na który pas auto chce się przeniesc."""
        if lane == self.LANE_RAMP:
            return self.LANE_RIGHT       # rozbiegowy zawsze celuje w prawy
        return 1 - lane                  # lewy <-> prawy

    def _can_change_lane(self, car, lane, target, x):
        """Decyduje czy zmiana pasa jest bezpieczna (i czy w ogóle warto)."""
        # Bezpieczeństwo - komórka docelowa pusta i z tyłu nikt nie nadjeżdża zbyt blisko
        cell_free = self.lanes[target][x] is None
        safe_behind = self._gap_behind(target, x) >= self.v_max
        if not (cell_free and safe_behind):
            return False

        # Z rozbiegowego: zmieniamy pas zawsze gdy bezpiecznie - chcemy się włączyć.
        if lane == self.LANE_RAMP:
            return True

        # Z głównego pasa: tylko gdy mamy zatłoczenie i sąsiad daje większy odstęp.
        gap_here = self._gap_ahead(lane, x)
        gap_there = self._gap_ahead(target, x)
        return gap_here < car['v'] and gap_there > gap_here

    # --- Faza 2 ---

    def _phase_move(self):
        """Standardowy NaSch: 1) przyspiesz 2) zwolnij do gap 3) losowo zwolnij 4) przesun."""
        new_lanes = [[None] * self.L for _ in range(3)]
        flow = 0  # ile aut wyjechało z głównych pasów (przepływ przez koniec drogi)

        for lane in range(3):
            for x in range(self.L):
                car = self.lanes[lane][x]
                if car is None:
                    continue

                # Auto rozbite stoi w miejscu i blokuje pas
                if car.get('crashed'):
                    new_lanes[lane][x] = car
                    continue

                # 1) Przyspiesz, ale nie powyżej v_max ani powyżej dostępnego odstępu
                # 2) Z prawdopodobieństwem p losowo zwolnij o 1
                v = min(car['v'] + 1, self.v_max, self._gap_ahead(lane, x))
                if random.random() < self.p:
                    v = max(v - 1, 0)
                car['v'] = v

                # 3) Przesuń auto do nowej komórki
                new_x = x + v
                limit = self._lane_limit(lane)

                if lane != self.LANE_RAMP and new_x >= self.L:
                    flow += 1                              # auto wyjechało poza scenę
                elif lane == self.LANE_RAMP and new_x >= limit:
                    new_lanes[lane][limit - 1] = car       # forsujemy stop na końcu rozbiegowego
                else:
                    new_lanes[lane][new_x] = car

        self.lanes = new_lanes
        return flow

    # --- Faza 3 ---

    def _phase_spawn(self):
        """Generujemy nowe auta na początkach pasów (otwarta granica)."""
        # Pasy główne: na komórce 0 z prawdopodobieństwem density
        for lane in (self.LANE_LEFT, self.LANE_RIGHT):
            if self.lanes[lane][0] is None and random.random() < self.density:
                self.lanes[lane][0] = self._make_car(self.v_max)

        # Pas rozbiegowy: rzadziej (×0.6) i z mniejszą prędkością początkową
        if self.lanes[self.LANE_RAMP][self.RAMP_START] is None \
                and random.random() < self.density * 0.6:
            self.lanes[self.LANE_RAMP][self.RAMP_START] = self._make_car(max(0, self.v_max - 2))

    def _make_car(self, v):
        """Tworzy nowe auto z unikalnym ID."""
        car = {'id': f"{self.sim_id}_{self.car_id_counter}", 'v': v}
        self.car_id_counter += 1
        return car

    # --- Statystyki ---

    def _compute_metrics(self):
        """Liczy gęstość (na pasach głównych) i średnią prędkość wszystkich aut."""
        cars_main = sum(1 for lane in (self.LANE_LEFT, self.LANE_RIGHT)
                        for c in self.lanes[lane] if c)
        density = cars_main / (self.L * 2)

        all_cars = [c for lane in self.lanes for c in lane if c]
        avg_speed = sum(c['v'] for c in all_cars) / len(all_cars) if all_cars else 0

        return density, avg_speed


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

        if parsed.path == '/styles.css':
            try:
                with open('styles.css', 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/css')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
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
