# AutobahnJam: Nagel-Schreckenberg Traffic Simulation

AutobahnJam is an interactive, web-based macroscopic traffic simulation engine built on top of the two-lane **Nagel-Schreckenberg (NaSch)** cellular automata model. It simulates realistic traffic flow dynamics, bottlenecks, and the "phantom traffic jam" phenomenon, explicitly incorporating an on-ramp merging lane.

## 🚀 Features

* **Real-Time Physics Engine:** Emulates vehicle progression, lane incentive/safety evaluation, and randomization (dawdling) entirely in Python.
* **On-Ramp Merging Dynamics:** Lane 2 serves as a dedicated on-ramp. Vehicles are strictly enforced to navigate to a predefined merging zone (`x >= 100`) before entering the main circulatory flow.
* **Holographic Control Station UI:** A purely vanilla HTML5/JS `<canvas>` presentation that utilizes crisp orthogonal Cartesian renders, simulating an advanced engineering dashboard.
* **Dynamic Parameter Sweeper:** Control maximum speed intervals, traffic density (inflow rate), visual timeframe stretching, and weather modes directly during runtime.
* **Macroscopic Data Export:** Extracts telemetry straight into an analytical CSV file containing `[Time, Density, Flow, Speed, and Maneuvers]` for post-simulation analytical processing.

## 🛠 Tech Stack

- **Backend:** Python 3 (Vanilla standard library `http.server` & `threading`).
- **Frontend:** HTML5, Context 2D Canvas, CSS3, vanilla JavaScript.
- **Charts:** Chart.js integration via CDN.

## 🚦 How to Run Locally

You do not need to install any heavy packages or `pip` modules. The entire simulation relies on standard libraries.

1. **Start the Backend Engine:**
   Open a terminal in the project directory and spin up the Python server:
   ```bash
   python3 server.py
   ```
   *The server will securely bind to port `8080` and run a daemon thread to maintain the simulation tick state.*

2. **Open the Interface:**
   Launch the `index.html` file in any modern web browser (Google Chrome or Mozilla Firefox recommended). 
   ```bash
   open index.html
   ```

3. **Engage the Simulation:**
   Hit `Start / Stop` on the UI timeline to kickstart the data flow. Modify parameters (such as enabling the `Weather: Pouring` configuration) to observe instantaneous model deviations. Once complete, hitting `Stop` invokes the diagnostic readout where you may safely download your CSV telemetry.

## ⚙️ Architecture 

- **State Persistence:** The `ServerState` object retains asynchronous continuity, maintaining sliding 30-tick windows of arrays independently from browser calls.
- **Data Transport:** JSON payloads are dispatched via classic polling requests mapping from the frontend's fetching loops exactly onto `/api/state` and `/api/config`.
- **Typing Strictness:** Built explicitly utilizing standard PEP8 parameters and Python `typing` constructs (`Dict`, `Tuple`, etc.) for immediate engineering legibility. 
