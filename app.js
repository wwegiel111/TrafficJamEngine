/* ===========================================================
 * TrafficJamEngine - front-end
 * Drives controls, polls /api/state, renders cars on canvas
 * =========================================================== */

const API_URL = window.location.origin;
const FETCH_INTERVAL = 150; // matches backend tick

// ----- Road layout constants used by renderer -----
const ROAD = {
    L: 150,             // cells on main lanes
    rampStart: 70,      // first ramp cell
    rampEnd: 115,       // last usable ramp cell
};

// ----- DOM lookup helper -----
const $ = id => document.getElementById(id);

// ----- Canvas + responsive sizing -----
const canvas = $('roadCanvas');
const ctx = canvas.getContext('2d');

// `view` holds CSS pixel coordinates derived from the current canvas size
const view = { width: 0, height: 0, laneWidth: 46, pyCenter: 0 };

function resizeCanvas() {
    // Match the backing store to displayed CSS size, accounting for HiDPI displays
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth;
    const cssHeight = canvas.clientHeight;

    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); // draw in CSS pixels

    view.width = cssWidth;
    view.height = cssHeight;
    view.pyCenter = cssHeight / 2;
    // Lane width scales with canvas height so ramps stay readable on any screen
    view.laneWidth = Math.max(28, Math.min(60, Math.round(cssHeight * 0.11)));
}

window.addEventListener('resize', resizeCanvas);

// ----- Controls state -----
let isRunning = false;
let isRaining = false;
let uiDensity = 10;

let simStats = { ticks: 0, flowSum: 0, speedSum: 0, maxFlow: 0, lanesSum: 0 };
let sessionData = [];

function resetStats() {
    simStats = { ticks: 0, flowSum: 0, speedSum: 0, maxFlow: 0, lanesSum: 0 };
    sessionData = [];
}

// ----- API helpers -----
function sendConfig(opts) {
    return fetch(`${API_URL}/api/config`, {
        method: 'POST',
        body: JSON.stringify(opts),
        headers: { 'Content-Type': 'application/json' },
    }).catch(err => console.error(err));
}

// ----- Sliders -----
const domDensity = $('paramDensity');
const domVmax = $('paramVmax');
const domSpeedMulti = $('paramSpeedMulti');
const inpDensity = $('inpDensity');
const inpVmax = $('inpVmax');
const inpSpeedMulti = $('inpSpeedMulti');

[domDensity, domVmax, domSpeedMulti].forEach(el => {
    el.addEventListener('input', e => {
        if (e.target === domDensity) {
            inpDensity.value = e.target.value;
            uiDensity = parseInt(e.target.value, 10);
        } else if (e.target === domVmax) {
            // 1 cell/step ≈ 27 km/h
            inpVmax.value = e.target.value * 27;
        } else if (e.target === domSpeedMulti) {
            inpSpeedMulti.value = e.target.value + 'x';
        }
    });
    el.addEventListener('change', () => {
        sendConfig({
            density: parseInt(domDensity.value, 10) / 100,
            v_max: parseInt(domVmax.value, 10),
            speed_multiplier: parseInt(domSpeedMulti.value, 10),
        });
    });
});

// ----- Buttons -----
const btnToggle = $('btnToggle');
btnToggle.addEventListener('click', () => {
    if (isRunning) {
        // Pausing -> show summary modal if we have any data
        if (simStats.ticks > 0) showSummary();
    } else {
        resetStats();
    }
    isRunning = !isRunning;
    updateToggleButton();
    sendConfig({ is_running: isRunning });
});

function updateToggleButton() {
    btnToggle.innerText = isRunning ? "Stop" : "Start";
    btnToggle.classList.toggle('btn-running', isRunning);
}

$('btnAccident').addEventListener('click', () => sendConfig({ trigger_accident: true }));
$('btnClearAccident').addEventListener('click', () => sendConfig({ clear_accident: true }));

$('btnWeather').addEventListener('click', () => {
    isRaining = !isRaining;
    const btn = $('btnWeather');
    btn.innerText = isRaining ? "🌧 Weather: Pouring" : "🌞 Weather: Dry";
    btn.classList.toggle('btn-rain', isRaining);
    // Higher dawdling probability under rain models the "fear effect"
    sendConfig({ p: isRaining ? 0.7 : 0.3 });
});

$('btnReset').addEventListener('click', () => {
    carsTracker = {};
    resetStats();
    sendConfig({
        density: parseInt(domDensity.value, 10) / 100,
        v_max: parseInt(domVmax.value, 10),
        speed_multiplier: parseInt(domSpeedMulti.value, 10),
        p: isRaining ? 0.7 : 0.3,
        reset: true,
    });
    flowChart.data.labels = [];
    flowChart.data.datasets[0].data = [];
    flowChart.data.datasets[1].data = [];
    flowChart.update();
});

// ----- Summary modal -----
function showSummary() {
    const m = Math.floor(simStats.ticks / 60);
    const s = simStats.ticks % 60;
    $('repTime').innerText = `${m} min ${s} sec (virtual)`;
    $('repSpeed').innerText = (simStats.speedSum / simStats.ticks).toFixed(1);
    $('repFlow').innerText = (simStats.flowSum / simStats.ticks).toFixed(3);
    $('repMaxFlow').innerText = simStats.maxFlow.toFixed(3);
    $('repLanes').innerText = simStats.lanesSum;
    $('summaryModal').style.display = 'flex';
}

$('btnExportCSV').addEventListener('click', () => {
    let csv = "data:text/csv;charset=utf-8,";
    csv += "Simulation Time [MM:SS];Density [%];Flow [veh/s];Average Speed [km/h];Lane Change Maneuvers\n";
    sessionData.forEach(row => {
        csv += `${row.time};${row.density};${row.flow};${row.speedKmh};${row.laneChanges}\n`;
    });
    const link = document.createElement("a");
    link.setAttribute("href", encodeURI(csv));
    link.setAttribute("download", `NaSch_Analysis_${new Date().getTime()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
});

// ----- Vehicle tracker (per-id animation state) -----
let carsTracker = {};

function updateTargets(lanes) {
    const L = lanes[0].length;
    const now = performance.now();

    for (let id in carsTracker) carsTracker[id].seen = false;

    for (let i = 0; i < lanes.length; i++) {
        for (let x = 0; x < L; x++) {
            const info = lanes[i][x];
            if (!info) continue;

            const id = info.id;
            if (!carsTracker[id]) {
                carsTracker[id] = {
                    x, targetX: x, startX: x,
                    lane: i, targetLane: i, startLane: i,
                    startTime: now, seen: true, speed: info.v,
                };
            } else {
                const car = carsTracker[id];
                car.seen = true;
                car.startX = car.x;
                car.startLane = car.lane;
                car.targetX = x;
                car.targetLane = i;
                car.startTime = now;
                car.speed = info.v;
                car.crashed = info.crashed || false;
            }
        }
    }
    for (let id in carsTracker) {
        if (!carsTracker[id].seen) delete carsTracker[id];
    }
}

// ----- Geometry helpers -----
function laneToScreen(cell, lane) {
    // Map cell index to horizontal pixels (slightly past edges so cars enter/exit smoothly)
    const pxStart = -view.width * 0.05;
    const pxEnd = view.width * 1.05;
    const t = cell / ROAD.L;
    const px = pxStart + t * (pxEnd - pxStart);

    const py = view.pyCenter;
    const lw = view.laneWidth;

    let offset;
    if (lane <= 1) {
        // smooth interpolation from left main (-lw/2) to right main (+lw/2)
        offset = -lw / 2 + lane * lw;
    } else {
        // smooth interpolation between right main (+lw/2) and ramp (1.5 * lw)
        const fraction = Math.max(0, Math.min(1, lane - 1.0));
        offset = lw / 2 + fraction * (1.5 * lw - lw / 2);
    }
    return { x: px, y: py + offset };
}

function rampPx(cell) {
    const pxStart = -view.width * 0.05;
    const pxEnd = view.width * 1.05;
    return pxStart + (cell / ROAD.L) * (pxEnd - pxStart);
}

// ----- Drawing primitives -----
function drawCarEntity(x, y, angle, speed, color, isCrashed) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);

    // Headlight cone for moving cars
    if (!isCrashed && speed > 0) {
        const reach = 60 + speed * 15;
        const grad = ctx.createLinearGradient(8, 0, reach, 0);
        grad.addColorStop(0, 'rgba(255,255,255,0.45)');
        grad.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.moveTo(8, -6);
        ctx.lineTo(reach, -25);
        ctx.lineTo(reach, 25);
        ctx.lineTo(8, 6);
        ctx.fill();
    }

    // Body
    ctx.shadowBlur = 10;
    ctx.shadowColor = color;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(-10, -6, 18, 12, 4);
    ctx.fill();

    // Lights / hazards
    if (isCrashed) {
        const blink = Math.floor(performance.now() / 400) % 2 === 0;
        if (blink) {
            ctx.shadowBlur = 15;
            ctx.shadowColor = '#f9e2af';
            ctx.fillStyle = '#f9e2af';
            ctx.fillRect(6, -5, 3, 3);
            ctx.fillRect(6, 2, 3, 3);
            ctx.fillRect(-11, -5, 3, 3);
            ctx.fillRect(-11, 2, 3, 3);
        }
    } else if (speed > 0.8) {
        ctx.shadowBlur = 10;
        ctx.shadowColor = '#ffffff';
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(8, -4, 3, 2);
        ctx.fillRect(8, 2, 3, 2);
    } else if (speed <= 0.2) {
        ctx.shadowBlur = 18;
        ctx.shadowColor = '#ff0000';
        ctx.fillStyle = '#ff0000';
        ctx.fillRect(-10, -5, 3, 3);
        ctx.fillRect(-10, 2, 3, 3);
    }

    // Cabin glass overlay
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(17,17,27,0.7)";
    ctx.beginPath();
    ctx.roundRect(-2, -4, 8, 8, 2);
    ctx.fill();

    ctx.restore();
}

function drawGrid() {
    ctx.strokeStyle = 'rgba(137,180,250,0.04)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < view.width; i += 40) { ctx.moveTo(i, 0); ctx.lineTo(i, view.height); }
    for (let j = 0; j < view.height; j += 40) { ctx.moveTo(0, j); ctx.lineTo(view.width, j); }
    ctx.stroke();
}

function drawRoadSurface() {
    const lw = view.laneWidth;
    const pyTop = view.pyCenter - lw;
    const pyBottomMain = view.pyCenter + lw;
    const pxStart = -view.width * 0.05;
    const pxEnd = view.width * 1.05;
    const pxRampS = rampPx(ROAD.rampStart);
    const pxRampE = rampPx(ROAD.rampEnd);

    ctx.fillStyle = '#2b2c3a';
    ctx.fillRect(pxStart, pyTop, pxEnd - pxStart, lw * 2);
    ctx.fillRect(pxRampS, pyBottomMain, pxRampE - pxRampS, lw);
}

function drawNeonFrames() {
    const lw = view.laneWidth;
    const pyTop = view.pyCenter - lw;
    const pyBottomMain = view.pyCenter + lw;
    const pyBottomRamp = view.pyCenter + lw * 2;
    const pxStart = -view.width * 0.05;
    const pxEnd = view.width * 1.05;
    const pxRampS = rampPx(ROAD.rampStart);
    const pxRampE = rampPx(ROAD.rampEnd);

    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(137,180,250,0.4)';
    ctx.shadowBlur = 8;
    ctx.shadowColor = 'rgba(137,180,250,0.4)';

    const segments = [
        [pxStart, pyTop, pxEnd, pyTop],
        [pxStart, pyBottomMain, pxRampS, pyBottomMain],
        [pxRampS, pyBottomRamp, pxRampE, pyBottomRamp],
        [pxRampS, pyBottomMain, pxRampS, pyBottomRamp],
        [pxRampE, pyBottomRamp, pxRampE, pyBottomMain],
        [pxRampE, pyBottomMain, pxEnd, pyBottomMain],
    ];

    ctx.beginPath();
    segments.forEach(([x1, y1, x2, y2]) => {
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;
}

function drawLaneMarkers() {
    const lw = view.laneWidth;
    const pyBottomMain = view.pyCenter + lw;
    const pxStart = -view.width * 0.05;
    const pxEnd = view.width * 1.05;
    const pxRampS = rampPx(ROAD.rampStart);
    const pxRampE = rampPx(ROAD.rampEnd);

    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(166,227,161,0.3)';

    // dashed centre divider
    ctx.setLineDash([20, 20]);
    ctx.beginPath();
    ctx.moveTo(pxStart, view.pyCenter);
    ctx.lineTo(pxEnd, view.pyCenter);
    ctx.stroke();

    // dashed ramp merge line
    ctx.setLineDash([10, 10]);
    ctx.beginPath();
    ctx.moveTo(pxRampS, pyBottomMain);
    ctx.lineTo(pxRampE, pyBottomMain);
    ctx.stroke();

    ctx.setLineDash([]);
}

function drawInjectionPorts() {
    const lw = view.laneWidth;
    const pyTop = view.pyCenter - lw;
    const pyBottomMain = view.pyCenter + lw;
    const pxStart = -view.width * 0.05;
    const pxEnd = view.width * 1.05;
    const pxRampS = rampPx(ROAD.rampStart);

    ctx.fillStyle = 'rgba(243,139,168,0.1)';
    ctx.fillRect(pxStart - 10, pyTop - 10, 20, lw * 2 + 20);
    ctx.fillRect(pxEnd - 10, pyTop - 10, 30, lw * 2 + 20);

    ctx.fillStyle = 'rgba(166,227,161,0.1)';
    ctx.fillRect(pxRampS - 10, pyBottomMain + 5, 20, lw - 10);
}

function drawRain() {
    if (!isRaining) return;
    ctx.fillStyle = 'rgba(137,180,250,0.15)';
    for (let i = 0; i < 200; i++) {
        ctx.fillRect(Math.random() * view.width, Math.random() * view.height, 1, 6 + Math.random() * 20);
    }
}

// ----- Main render loop -----
function renderFrame(time) {
    ctx.clearRect(0, 0, view.width, view.height);
    ctx.fillStyle = '#181825';
    ctx.fillRect(0, 0, view.width, view.height);

    drawGrid();
    drawRoadSurface();
    drawNeonFrames();
    drawRain();
    drawLaneMarkers();
    drawInjectionPorts();

    const vmax = parseInt(domVmax.value, 10) || 2;

    for (let id in carsTracker) {
        const car = carsTracker[id];

        // Linear interpolation toward latest backend snapshot
        let progress = (time - car.startTime) / FETCH_INTERVAL;
        progress = Math.max(0, Math.min(1.2, progress));

        car.x = car.startX + (car.targetX - car.startX) * progress;
        car.lane = car.startLane + (car.targetLane - car.startLane) * progress;

        if (car.x >= ROAD.L) continue;

        // Hue from red (slow) to green (fast)
        const hue = (car.speed / vmax) * 120;
        const color = car.crashed ? '#3f404d'
                                  : `hsl(${Math.max(0, Math.min(120, hue))}, 85%, 60%)`;

        // Tangent angle so the car body follows the road / ramp curve
        const p1 = laneToScreen(car.x, car.lane);
        const p2 = laneToScreen(car.x + 0.1, car.lane);
        const angle = Math.atan2(p2.y - p1.y, p2.x - p1.x);

        drawCarEntity(p1.x, p1.y, angle, car.speed, color, car.crashed);
    }

    requestAnimationFrame(renderFrame);
}

// ----- Chart -----
const flowChart = new Chart($('flowChart').getContext('2d'), {
    type: 'line',
    data: {
        labels: [],
        datasets: [
            {
                label: 'Current Flow (vehicles / step)',
                borderColor: '#89b4fa',
                backgroundColor: 'rgba(137,180,250,0.2)',
                data: [], fill: true, tension: 0.4,
            },
            {
                label: 'Reference Line (Maximum Theoretical Capacity)',
                borderColor: '#f38ba8',
                borderDash: [5, 5],
                data: [], fill: false, tension: 0.1,
            },
        ],
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            y: { beginAtZero: true, max: 1.5,
                 grid: { color: '#3f404d' }, ticks: { color: '#a0a0a0' } },
            x: { grid: { color: '#3f404d' },
                 ticks: { color: '#a0a0a0', maxTicksLimit: 15 } },
        },
        plugins: { legend: { labels: { color: '#e4e4e4', font: { family: 'Inter' } } } },
        animation: false,
    },
});

// ----- Polling loop -----
const stateDensity = $('valDensity');
const stateFlow = $('valFlow');
const stateLanes = $('valLanes');

setInterval(() => {
    fetch(`${API_URL}/api/state`)
        .then(res => res.json())
        .then(data => {
            stateDensity.innerText = (data.stats.density * 100).toFixed(1) + '%';
            stateFlow.innerText = data.stats.flow.toFixed(3);
            stateLanes.innerText = data.stats.lane_changes;

            const speedKmh = (data.stats.speed || 0) * 27;
            $('valSpeed').innerText = speedKmh.toFixed(1) + ' km/h';

            if (isRunning !== data.is_running) {
                isRunning = data.is_running;
                updateToggleButton();
            }

            if (data.lanes && data.lanes[0]) {
                updateTargets(data.lanes);
                if (isRunning) recordSample(data, speedKmh);
            }
        })
        .catch(() => console.error("Waiting for backend API..."));
}, FETCH_INTERVAL);

function recordSample(data, speedKmh) {
    const flow = data.stats.flow;
    const density = data.stats.density;
    const trueTicks = data.stats.total_ticks;

    simStats.ticks = trueTicks;
    simStats.flowSum += flow;
    simStats.speedSum += speedKmh;
    simStats.lanesSum = data.stats.lane_changes;
    if (flow > simStats.maxFlow) simStats.maxFlow = flow;

    const m = Math.floor(trueTicks / 60);
    const s = (trueTicks % 60).toString().padStart(2, '0');
    const timeString = `${m}:${s}`;

    sessionData.push({
        time: timeString,
        density: (density * 100).toFixed(1),
        flow: flow.toFixed(3),
        speedKmh: speedKmh.toFixed(1),
        laneChanges: data.stats.lane_changes,
    });

    // Theoretical NaSch reference: q_max = min(v_max * k, 1 - k); 2 lanes
    const vmax = parseInt(domVmax.value, 10);
    const qMax = Math.min(density * vmax, 1.0 - density);
    const referenceFlow = qMax * 2;

    flowChart.data.labels.push(timeString);
    flowChart.data.datasets[0].data.push(flow);
    flowChart.data.datasets[1].data.push(referenceFlow);

    if (flowChart.data.labels.length > 40) {
        flowChart.data.labels.shift();
        flowChart.data.datasets[0].data.shift();
        flowChart.data.datasets[1].data.shift();
    }
    flowChart.update();
}

// ----- Bootstrap -----
resizeCanvas();
requestAnimationFrame(renderFrame);

sendConfig({
    density: uiDensity / 100,
    p: 0.3,
    v_max: 2,
    is_running: false,
});
