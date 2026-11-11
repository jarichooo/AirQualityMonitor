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
    assert.doesNotMatch(results.textContent, /Device sim-farm|Simulated input|Input through|Forecast target|Forecasts are model estimates|Predict now/);
    for (const card of withClass(results, "forecast-card")) {
        assert.ok(card.textContent.indexOf("Predicted values in 2 hours") < card.textContent.indexOf("Review the forecast."));
    }

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
    assert.match(cards[0].textContent, /Review the forecast/);
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
        assert.match(results.textContent, /Previous advice: check ventilation/);
        assert.doesNotMatch(results.textContent, /2026-10-30|Previous forecast target|Predict now/);
        if (status === "unavailable") assert.equal(withClass(results, "forecast-value").length, 0);
        assert.match(results.textContent, status === "pending" ? /Updating…/ : status === "error" ? /Could not update/ : /New readings are needed/);
        assert.equal(refreshDelay, status === "pending" ? 3000 : 20000);
    }

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "unavailable", explanation: null, last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /Live forecast updated\. Showing the previous advice/);
    assert.doesNotMatch(results.textContent, /Predict now/);
    assert.equal(refreshDelay, 20000);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "unavailable", explanation: null,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /No advice yet\./);
    assert.doesNotMatch(results.textContent, /Predict now/);
    assert.equal(refreshDelay, 20000);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "pending", explanation: null,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /Loading…/);
    assert.equal(refreshDelay, 3000);
    for (const reason of ["QWEN_TIMEOUT", "QWEN_UNAVAILABLE"]) {
        payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
            explanation_status: "error", explanation: null, reason,
        })] });
        intervalCallback();
        await settle();
        await settle();
        assert.match(results.textContent, /Could not load the advice/);
        assert.match(results.textContent, /Try again after a new reading arrives/);
        assert.doesNotMatch(results.textContent, /Qwen|Ollama|forecast worker|logs/i);
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
    assert.match(results.textContent, /Previous advice: check ventilation/);
    assert.match(panel.textContent, /Could not refresh forecasts/);
    assert.equal(refreshDelay, 15000);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation: "New completed guidance.", last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /New completed guidance/);
    assert.doesNotMatch(results.textContent, /Previous advice/);
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
    assert.doesNotMatch(results.textContent, /New completed guidance/);
    assert.match(results.textContent, /No results yet/);

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
    assert.match(results.textContent, /Review the forecast/);
    assert.equal(refreshDelay, 20000);
    console.log("PASS: forecast rendering without predict button, chart-synced updates, polling, and text-only rendering.");
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
