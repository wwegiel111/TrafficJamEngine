"""
HTTP server for the NaSch traffic simulation.
"""

import json
import logging
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
from simulation import TrafficSimulation


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("trafficjam")


# ====================================================================
# SERVER STATE AND WORKER
# ====================================================================

class ServerState:
    """Current run plus rolling statistics shared between worker and HTTP."""

    def __init__(self):
        self.sim_params = {
            'density': config.INITIAL_DENSITY,
            'p': config.INITIAL_P,
            'v_max': config.INITIAL_V_MAX,
            'speed_multiplier': config.INITIAL_SPEED_MULTIPLIER,
        }
        self.is_running = False

        # Sliding window of recent samples
        self.flow_history = []
        self.density_history = []
        self.speed_history = []

        self.total_lane_changes = 0
        self.total_sim_ticks = 0

        self.live_sim = self._create_sim()

    def _create_sim(self):
        return TrafficSimulation(
            L=config.ROAD_LENGTH,
            density=self.sim_params['density'],
            p=self.sim_params['p'],
            v_max=self.sim_params['v_max'],
        )

    def reset_simulation(self):
        """Wipe the run and start fresh with current parameters."""
        self.live_sim = self._create_sim()
        self.flow_history.clear()
        self.density_history.clear()
        self.speed_history.clear()
        self.total_lane_changes = 0
        self.total_sim_ticks = 0
        logger.info("Simulation reset")


# Single shared instance accessed by both threads.
state = ServerState()


def sim_worker():
    """
    Background loop: tick the simulation while `is_running` is true.
    Runs every TICK_INTERVAL / speed_multiplier seconds.
    """
    while True:
        if state.is_running:
            flow, lane_changes, density, speed = state.live_sim.step()

            state.total_sim_ticks += 1
            state.total_lane_changes += lane_changes

            state.flow_history.append(flow)
            state.density_history.append(density)
            state.speed_history.append(speed)

            # Trim oldest samples to keep the window bounded
            while len(state.flow_history) > config.HISTORY_WINDOW:
                state.flow_history.pop(0)
                state.density_history.pop(0)
                state.speed_history.pop(0)

        time.sleep(config.TICK_INTERVAL / state.sim_params['speed_multiplier'])


# ====================================================================
# HTTP API
# ====================================================================

class APIServer(BaseHTTPRequestHandler):
    """REST endpoints used by the front-end."""

    # Quiet the default per-request access logging; we use our own logger.
    def log_message(self, format, *args):
        logger.debug("%s - %s", self.address_string(), format % args)

    # ----- CORS / health -----

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()

    # ----- GET routes

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == '/':
            self._serve_file('index.html', 'text/html')
        elif path == '/styles.css':
            self._serve_file('styles.css', 'text/css')
        elif path == '/app.js':
            self._serve_file('app.js', 'application/javascript')
        elif path == '/api/state':
            self._serve_state()
        else:
            self._send_status(404)

    # ----- POST routes

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == '/api/config':
            self._handle_config_update()
        else:
            self._send_status(404)

    # ----- Helpers

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_status(self, code, body=b''):
        self.send_response(code)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors_headers()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filename, content_type):
        try:
            with open(filename, 'rb') as f:
                content = f.read()
        except FileNotFoundError:
            logger.warning("Missing static file: %s", filename)
            self._send_status(404, f"{filename} not found.".encode('utf-8'))
            return

        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_state(self):
        """Snapshot of lanes + averaged statistics."""
        flow_h = state.flow_history
        density_h = state.density_history
        speed_h = state.speed_history

        avg = lambda h: (sum(h) / len(h)) if h else 0

        self._send_json({
            'lanes': state.live_sim.lanes,
            'is_running': state.is_running,
            'stats': {
                'density': avg(density_h),
                'flow': avg(flow_h),
                'lane_changes': state.total_lane_changes,
                'speed': avg(speed_h),
                'ticks': len(flow_h),
                'total_ticks': state.total_sim_ticks,
            },
        })

    def _handle_config_update(self):
        """Apply incoming JSON to the live simulation."""
        try:
            length = int(self.headers.get('Content-Length', '0'))
            data = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Bad config payload: %s", exc)
            self._send_json({'error': 'invalid JSON'}, code=400)
            return

        # --- numeric parameters
        if 'density' in data:
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

        # --- run state
        if 'is_running' in data:
            state.is_running = bool(data['is_running'])
            logger.info("Run state: %s", "running" if state.is_running else "paused")

        # --- special events
        lane = config.ACCIDENT_LANE
        cell = config.ACCIDENT_CELL

        if data.get('trigger_accident'):
            state.live_sim.lanes[lane][cell] = {'id': 'CRASH_1', 'v': 0, 'crashed': True}
            logger.info("Accident triggered at lane=%s cell=%s", lane, cell)

        if data.get('clear_accident'):
            existing = state.live_sim.lanes[lane][cell]
            if existing and existing.get('crashed'):
                state.live_sim.lanes[lane][cell] = None
                logger.info("Accident cleared")

        if data.get('reset'):
            state.reset_simulation()

        self._send_json({'status': 'ok'})


# ====================================================================
# Entry point
# ====================================================================

def main():
    threading.Thread(target=sim_worker, daemon=True).start()

    port = int(os.environ.get("PORT", config.DEFAULT_PORT))
    server = HTTPServer((config.HOST, port), APIServer)

    logger.info("Simulation engine listening on http://%s:%s", config.HOST, port)
    logger.info("Open http://localhost:%s/ in your browser", port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server")
        server.server_close()


if __name__ == "__main__":
    main()
