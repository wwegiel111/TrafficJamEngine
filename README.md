# TrafficJamEngine

Interactive web simulation of a two-lane highway with an on-ramp, built on the
**Nagel-Schreckenberg (NaSch)** cellular automaton. The Python backend ticks the
model in a background thread and exposes a small REST API; the browser polls
that API and renders cars, ramps, weather, and accident events on a canvas.

## Features

- Real-time NaSch model (acceleration, braking, randomization, motion)
- On-ramp merging logic with safe lane changes
- Adjustable parameters at runtime: density, max speed, simulation speed, weather
- Accident events that can be triggered and cleared from the UI
- Live flow chart with theoretical capacity reference line
- CSV export of the recorded session
- Responsive UI that scales from 14" laptops up to 4K displays

## Project layout

```
.
├── config.py        # All tunable constants (geometry, defaults, port)
├── simulation.py    # Pure NaSch simulation kernel
├── server.py        # HTTP server, ServerState, background worker
├── app.js           # Front-end logic (polling, controls, rendering)
├── index.html       # Page structure
├── styles.css       # Responsive layout and theme
└── README.md
```

## Running locally

The server uses only Python's standard library, so no `pip install` is needed.

```bash
python3 server.py
```

Then open <http://localhost:8080/> in your browser.

The port can be overridden with the `PORT` environment variable:

```bash
PORT=9000 python3 server.py
```

## API

| Method | Path           | Description                                  |
|--------|----------------|----------------------------------------------|
| GET    | `/`            | Serves `index.html`                          |
| GET    | `/styles.css`  | Stylesheet                                   |
| GET    | `/app.js`      | Front-end script                             |
| GET    | `/api/state`   | JSON snapshot of lanes + averaged statistics |
| POST   | `/api/config`  | Apply runtime configuration changes          |

### `POST /api/config` payload (all fields optional)

```json
{
  "density": 0.15,
  "p": 0.3,
  "v_max": 3,
  "speed_multiplier": 2.0,
  "is_running": true,
  "trigger_accident": false,
  "clear_accident": false,
  "reset": false
}
```

## Model parameters

| Symbol        | Meaning                                 | Default |
|---------------|-----------------------------------------|---------|
| `L`           | Cells per main lane                     | 150     |
| `density`     | Spawn probability per step              | 0.10    |
| `v_max`       | Maximum speed (cells per step)          | 2       |
| `p`           | Random deceleration probability         | 0.30    |
| `RAMP_START`  | First cell of the on-ramp               | 70      |
| `RAMP_LENGTH` | Last usable cell on the on-ramp         | 115     |

All defaults live in `config.py` and can be edited in one place.

## Architecture notes

- **Simulation kernel (`simulation.py`).** Pure Python class. Each `step()` runs
  three phases: lane changes, forward motion, spawn. Returns `(flow,
  lane_changes, density, avg_speed)`.
- **Worker thread (`server.py`).** Calls `step()` every
  `TICK_INTERVAL / speed_multiplier` seconds while the run is active. Pushes
  samples into bounded sliding-window lists.
- **HTTP layer.** `BaseHTTPRequestHandler` with small helper methods
  (`_serve_file`, `_serve_state`, `_handle_config_update`). Returns JSON with
  proper CORS headers.
- **Front-end.** `app.js` polls `/api/state` every 150 ms, interpolates car
  positions between snapshots, and draws everything in CSS pixels with HiDPI
  scaling so the canvas stays sharp on Retina displays.

## License

Personal academic project.
