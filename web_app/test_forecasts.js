// Run: node web_app/test_forecasts.js. No database or browser required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "static/forecasts.js"), "utf8");

class Element {
    constructor(tag = "div") {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.attributes = {};
        this.className = "";
        this.dataset = {};
        this.disabled = false;
        this.hidden = false;
        this.listeners = {};
        this._textContent = "";
    }
    set textContent(value) { this._textContent = String(value); this.children = []; }
    get textContent() { return this._textContent + this.children.map((child) => child.textContent).join(""); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._textContent = ""; this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, callback) { this.listeners[type] = callback; }
}

const descendants = (root) => [root, ...root.children.flatMap(descendants)];
const withClass = (root, className) => descendants(root).filter((node) => node.className.split(/\s+/).includes(className));
const settle = () => new Promise((resolve) => setImmediate(resolve));

async function run() {
    const panel = new Element("section");
    panel.dataset.forecastUrl = "/api/forecasts";
    const results = new Element("div");
    const guidanceBanner = new Element("section");
    guidanceBanner.className = "dashboard-notification-slot";
    const guidanceResults = new Element("div");
    const listeners = {};
    const payloads = [];
    let intervalCallback;
    let refreshDelay;
    let fetchCount = 0;

    const document = {
        hidden: false,
        createElement: (tag) => new Element(tag),
        getElementById: (id) => ({
            "forecast-panel": panel,
            "forecast-results": results,
            "dashboard-notification-slot": guidanceBanner,
            "forecast-guidance-results": guidanceResults,
        }[id] || null),
        addEventListener: (type, callback) => { listeners[type] = callback; },
    };
    const window = {
        setTimeout: (callback, delay) => {
            if (delay !== 10000) { intervalCallback = callback; refreshDelay = delay; }
            return 1;
        },
        clearTimeout: () => {},
        location: { assign: () => assert.fail("Unexpected redirect") },
    };
    const response = (payload, status = 200) => ({
        ok: status >= 200 && status < 300,
        status,
        redirected: false,
        json: async () => payload,
    });
    const prediction = (label, severity, value = 12.5) => ({ label, severity, value, unit: "ppm" });
    const forecast = (device_id, scenario, severity, overrides = {}) => ({
        device_id,
        forecast_id: `${device_id}-forecast`,
        data_class: "SIMULATED",
        scenario,
        status: "available",
        input_cutoff_at: "2026-10-30T01:00:00Z",
        target_at: "2026-10-30T03:00:00Z",
        predictions: [prediction("Ammonia", severity)],
        explanation_status: "available",
        explanation: "Review the forecast.",
        ...overrides,
    });

    payloads.push({
        configured: true,
        forecasts: [
            forecast("sim-farm-good", "Good", "normal"),
            forecast("sim-farm-mid", "Warning", "warning"),
            forecast("sim-farm-bad", "Critical", "critical"),
            forecast("sim-farm-unknown", null, "not-a-category", {
                predictions: [prediction("<img src=x onerror=alert(1)>", "not-a-category")],
            }),
        ],
    });

    vm.runInNewContext(source, {
        AbortController: class { constructor() { this.signal = {}; } abort() {} },
        Intl,
        URL,
        console,
        document,
        fetch: async () => {
            fetchCount += 1;
            const entry = payloads.shift();
            const payload = typeof entry === "function" ? await entry() : entry;
            if (payload instanceof Error) throw payload;
            return response(payload);
        },
        window,
    });
    await settle();
    await settle();

    assert.equal(panel.attributes["aria-busy"], "false");
    assert.equal(refreshDelay, 20000);
    assert.equal(withClass(results, "forecast-legend").length, 0);
    assert.equal(withClass(results, "is-normal").some((node) => /Normal/.test(node.textContent)), true);
    assert.equal(withClass(results, "is-warning").some((node) => /Warning/.test(node.textContent)), true);
    assert.equal(withClass(results, "is-critical").some((node) => /Critical/.test(node.textContent)), true);
    assert.equal(withClass(results, "forecast-value").every((node) => node.attributes["aria-label"]), true);
    assert.equal(withClass(results, "is-unavailable").some((node) => /Unavailable/.test(node.textContent)), true);
    assert.equal(descendants(results).some((node) => node.tagName === "IMG"), false);
    assert.match(results.textContent, /<img src=x onerror=alert\(1\)>/);
    assert.doesNotMatch(results.textContent, /Review the forecast|Device sim-farm|Simulated input|Input through|Forecast target|Forecasts are model estimates|Predict now/);
    assert.match(guidanceResults.textContent, /Review the forecast/);
    assert.equal(descendants(guidanceResults).some((node) => node.tagName === "IMG"), false);
    assert.equal(guidanceBanner.className, "dashboard-notification-slot is-critical");

    const progression = [
        forecast("sim-farm-good", "Good", "normal", {
            status: "unavailable",
            reason: "Simulation stage completed",
            predictions: [],
            explanation_status: "unavailable",
            explanation: null,
        }),
        forecast("sim-farm-mid", "Warning", "warning"),
    ];
    payloads.push({
        configured: true,
        forecasts: progression,
    });
    intervalCallback();
    await settle();
    await settle();

    const cards = withClass(results, "forecast-card");
    assert.equal(cards.length, 1);
    assert.equal(withClass(cards[0], "forecast-value")[0].className.includes("is-warning"), true);
    assert.doesNotMatch(cards[0].textContent, /Review the forecast/);
    assert.match(guidanceResults.textContent, /Review the forecast/);
    assert.equal(guidanceBanner.className, "dashboard-notification-slot is-warning");
    assert.deepEqual(progression.map((item) => item.device_id), ["sim-farm-good", "sim-farm-mid"]);
    assert.doesNotMatch(results.textContent, /Simulation stage completed|Guidance is unavailable|sim-farm-good/);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-bad", "Critical", "critical", {
        status: "unavailable", predictions: [], explanation: null, explanation_status: "unavailable",
        reason: "The saved forecast is stale or has an invalid input time.",
    }), progression[0]] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(withClass(results, "forecast-card").length, 0);
    assert.equal(results.textContent, "No results yet.");
    assert.equal(guidanceResults.textContent, "No forecast yet.");
    assert.equal(guidanceBanner.className, "dashboard-notification-slot");
    assert.doesNotMatch(results.textContent, /stale|Simulation stage completed|Device/);

    const savedGuidance = {
        text: "Previous advice: check ventilation.",
        input_cutoff_at: "2026-10-30T01:00:00Z",
        target_at: "2026-10-30T03:00:00Z",
    };
    for (const status of ["pending", "error", "unavailable"]) {
        payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
            status: status === "unavailable" ? "unavailable" : "available",
            explanation_status: status, explanation: null, last_guidance: savedGuidance,
        })] });
        intervalCallback();
        await settle();
        await settle();
        assert.match(guidanceResults.textContent, /Previous advice: check ventilation/);
        assert.doesNotMatch(results.textContent, /Previous advice/);
        assert.doesNotMatch(guidanceResults.textContent, /2026-10-30|Previous forecast target|Predict now/);
        if (status === "unavailable") assert.equal(withClass(results, "forecast-value").length, 0);
        assert.match(guidanceResults.textContent, status === "pending" ? /Updating…/ : status === "error" ? /Could not update/ : /New readings are needed/);
        assert.equal(refreshDelay, status === "pending" ? 3000 : 20000);
    }

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "unavailable", explanation: null, last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(guidanceResults.textContent, /Live forecast updated\. Showing the previous output/);
    assert.doesNotMatch(results.textContent, /Previous advice/);
    assert.doesNotMatch(guidanceResults.textContent, /Predict now/);
    assert.equal(refreshDelay, 20000);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "unavailable", explanation: null,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(withClass(guidanceResults, "prediction-banner-text").length, 2);
    assert.match(guidanceResults.textContent, /Ammonia is forecast to reach 12\.5 ppm \(warning/);
    assert.match(guidanceResults.textContent, /Recommended checks: .*air exchange, ventilation fans, and litter or gas buildup/);
    assert.equal(withClass(guidanceResults, "is-muted").length, 0);
    assert.doesNotMatch(results.textContent, /Ammonia is forecast to reach|Recommended checks|No forecast yet|No advice yet/);
    assert.doesNotMatch(guidanceResults.textContent, /Predict now/);
    assert.equal(refreshDelay, 20000);

    payloads.push({ configured: true, forecasts: [forecast("esp32-demo", null, "critical", {
        data_class: "RECORDED",
        scenario: null,
        explanation_status: "unavailable",
        explanation: null,
        predictions: [
            { key: "humidity_perc", label: "Humidity", severity: "critical", value: 86.4, unit: "%" },
            { key: "temperature_c", label: "Temperature", severity: "normal", value: 26.4, unit: "°C" },
            { key: "co2_ppm", label: "CO2", severity: "normal", value: 780, unit: "ppm" },
            { key: "mq137_ppm", label: "Ammonia", severity: "normal", value: 12.5, unit: "ppm" },
        ],
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(withClass(guidanceResults, "prediction-banner-text").length, 2);
    assert.match(guidanceResults.textContent, /^Forecast for .*Humidity is forecast to reach 86\.4 % \(critical, above the 80% warning upper limit; normal range is 50–70%\)\. Remaining monitored sensors \(Temperature, CO2, and Ammonia\) are predicted to remain within normal thresholds\.Recommended checks: .*airflow, moisture or condensation buildup, and ventilation equipment before predicted critical conditions develop\.$/);
    assert.equal(guidanceBanner.className, "dashboard-notification-slot is-critical");

    payloads.push({ configured: true, forecasts: [forecast("esp32-demo", null, "critical", {
        data_class: "RECORDED",
        scenario: null,
        explanation_status: "unavailable",
        explanation: null,
        predictions: [{ key: "humidity_perc", label: "Humidity", severity: "critical", value: 34.2, unit: "%" }],
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(guidanceResults.textContent, /Humidity is forecast to reach 34\.2 % \(critical, below the 40% warning lower limit; normal range is 50–70%\)/);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-bad", "Critical", "critical", {
        explanation_status: "unavailable",
        explanation: null,
        predictions: [
            prediction("Ammonia", "critical", 6000),
            prediction("Methane", "warning", 15),
            prediction("CO2", "normal", 500),
        ],
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(withClass(guidanceResults, "prediction-banner-text").length, 2);
    assert.match(guidanceResults.textContent, /Ammonia is forecast to reach 6,000 ppm \(critical, above the 5,000 ppm warning upper limit; normal range is 0–1,000 ppm\); Methane is forecast to reach 15 ppm \(warning, above the 10 ppm normal limit and within the 20 ppm warning ceiling\)\. CO2 is predicted to remain within normal thresholds\./);
    assert.match(guidanceResults.textContent, /Recommended checks:/);
    assert.equal(guidanceBanner.className, "dashboard-notification-slot is-critical");

    payloads.push({ configured: true, forecasts: [forecast("esp32-demo", null, "normal", {
        data_class: "RECORDED",
        scenario: null,
        explanation_status: "unavailable",
        explanation: null,
        predictions: [
            { key: "temperature_c", label: "Temperature", severity: "normal", value: 26.4, unit: "°C" },
            { key: "humidity_perc", label: "Humidity", severity: "normal", value: 61.2, unit: "%" },
            { key: "co2_ppm", label: "CO2", severity: "normal", value: 780, unit: "ppm" },
            { key: "mq137_ppm", label: "Ammonia", severity: "normal", value: 12.5, unit: "ppm" },
        ],
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(withClass(guidanceResults, "prediction-banner-text").length, 2);
    assert.match(guidanceResults.textContent, /all 4 monitored sensors \(Temperature, Humidity, CO2, and Ammonia\) are predicted to remain within normal project thresholds, including Temperature \(26\.4 °C\), Humidity \(61\.2 %\), and CO2 \(780 ppm\)\./);
    assert.match(guidanceResults.textContent, /No critical or warning levels are predicted for this window/);
    assert.equal(guidanceBanner.className, "dashboard-notification-slot is-normal");

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-good", "Good", "normal", {
        explanation_status: "unavailable", explanation: null,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(guidanceResults.textContent, /Simulated forecast .* indicates Ammonia \(12\.5 ppm\) is predicted to remain within normal project thresholds\./);
    assert.equal(guidanceBanner.className, "dashboard-notification-slot is-normal");

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-unknown", null, "not-a-category", {
        explanation_status: "unavailable",
        explanation: null,
        predictions: [prediction("Ammonia", "not-a-category")],
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(guidanceResults.textContent, "No forecast yet.");
    assert.equal(withClass(guidanceResults, "is-muted").length, 1);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "pending", explanation: null,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(guidanceResults.textContent, /Loading forecast…/);
    assert.equal(refreshDelay, 3000);
    for (const reason of ["QWEN_TIMEOUT", "QWEN_UNAVAILABLE"]) {
        payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
            explanation_status: "error", explanation: null, reason,
        })] });
        intervalCallback();
        await settle();
        await settle();
        assert.match(guidanceResults.textContent, /Could not load the forecast summary/);
        assert.match(guidanceResults.textContent, /Try again after a new reading arrives/);
        assert.doesNotMatch(guidanceResults.textContent, /Qwen|Ollama|forecast worker|logs/i);
        assert.equal(withClass(results, "forecast-value").length, 1);
        assert.equal(refreshDelay, 20000);
    }

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "pending", explanation: null, last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    payloads.push(new Error("Offline"));
    intervalCallback();
    await settle();
    assert.match(guidanceResults.textContent, /Previous advice: check ventilation/);
    assert.match(panel.textContent, /Could not refresh forecasts/);
    assert.equal(refreshDelay, 15000);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation: "Humidity is forecast to reach 85.49%.\n\nInspect ventilation and airflow.",
        last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(withClass(guidanceResults, "prediction-banner-text").length, 2);
    assert.match(guidanceResults.textContent, /Humidity is forecast to reach 85\.49%/);
    assert.match(guidanceResults.textContent, /Inspect ventilation and airflow/);
    assert.doesNotMatch(guidanceResults.textContent, /Previous advice/);
    assert.doesNotMatch(results.textContent, /Humidity is forecast to reach/);
    assert.doesNotMatch(panel.textContent, /Could not refresh/);
    assert.equal(refreshDelay, 20000);

    assert.equal(typeof listeners["charts:updated"], "function");
    let releaseFirstFetch;
    payloads.push(() => new Promise((resolve) => {
        releaseFirstFetch = () => resolve({
            configured: true,
            forecasts: [forecast("sim-farm-mid", "Warning", "warning", { predictions: [prediction("Ammonia", "warning", 18.75)] })],
        });
    }));
    payloads.push({
        configured: true,
        forecasts: [forecast("sim-farm-mid", "Warning", "warning", { predictions: [prediction("Ammonia", "critical", 42.25)] })],
    });
    listeners["charts:updated"]();
    await settle();
    listeners["charts:updated"]();
    releaseFirstFetch();
    await settle();
    await settle();
    await settle();
    assert.match(results.textContent, /42\.25 ppm/);

    payloads.push({ configured: true, forecasts: [] });
    intervalCallback();
    await settle();
    await settle();
    assert.doesNotMatch(guidanceResults.textContent, /Inspect ventilation and airflow|Review the forecast/);
    assert.match(results.textContent, /No results yet/);
    assert.match(guidanceResults.textContent, /No forecast yet/);

    assert.equal(typeof listeners.visibilitychange, "function");
    document.hidden = true;
    const previousFetchCount = fetchCount;
    intervalCallback();
    await settle();
    assert.equal(fetchCount, previousFetchCount);
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning")] });
    document.hidden = false;
    listeners.visibilitychange();
    await settle();
    await settle();
    assert.equal(fetchCount, previousFetchCount + 1);
    assert.match(guidanceResults.textContent, /Review the forecast/);
    assert.equal(refreshDelay, 20000);
    console.log("PASS: forecast values and top banner summary rendering, chart-synced updates, polling, and text-only rendering.");
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
