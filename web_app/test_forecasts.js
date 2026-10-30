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
    panel.dataset.predictionUrl = "/api/predictions";
    panel.dataset.predictionToken = "prediction-token";
    const results = new Element("div");
    const predictionControls = new Element("div");
    predictionControls.hidden = true;
    const predictButton = new Element("button");
    predictButton.disabled = true;
    const predictionStatus = new Element("p");
    const listeners = {};
    const payloads = [];
    let intervalCallback;
    let refreshDelay;
    let fetchCount = 0;
    let predictionState = {
        configured: true, status: "completed", request_id: "request-1", device_id: "sim-farm-good",
        input_row_id: 120, cooldown_seconds: 12, message: "Prediction complete.", can_predict: false,
    };
    let postResponse = {
        status: 202,
        payload: {
            configured: true, status: "queued", request_id: "request-2", device_id: "sim-farm-good",
            input_row_id: 121, cooldown_seconds: 60, message: "Prediction queued.", can_predict: false,
        },
    };
    let postCount = 0;

    const document = {
        hidden: false,
        createElement: (tag) => new Element(tag),
        getElementById: (id) => ({
            "forecast-panel": panel,
            "forecast-results": results,
            "prediction-controls": predictionControls,
            "predict-now": predictButton,
            "prediction-status": predictionStatus,
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
    const prediction = (label, severity) => ({ label, severity, value: 12.5, unit: "ppm" });
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
        fetch: async (url, options = {}) => {
            fetchCount += 1;
            if (url === "/api/predictions") {
                if (options.method === "POST") {
                    postCount += 1;
                    assert.deepEqual(JSON.parse(options.body), { csrf_token: "prediction-token" });
                    return response(postResponse.payload, postResponse.status);
                }
                return response(predictionState);
            }
            const payload = payloads.shift();
            if (payload instanceof Error) throw payload;
            return response(payload);
        },
        window,
    });
    await settle();
    await settle();

    assert.equal(panel.attributes["aria-busy"], "false");
    assert.equal(refreshDelay, 3000);
    assert.equal(predictionControls.hidden, false);
    assert.equal(predictButton.disabled, true);
    assert.equal(predictButton.textContent, "Predict now");
    assert.match(predictionStatus.textContent, /12 seconds/);
    assert.equal(withClass(results, "forecast-legend").length, 0);
    assert.equal(withClass(results, "is-normal").some((node) => /Normal/.test(node.textContent)), true);
    assert.equal(withClass(results, "is-warning").some((node) => /Warning/.test(node.textContent)), true);
    assert.equal(withClass(results, "is-critical").some((node) => /Critical/.test(node.textContent)), true);
    assert.equal(withClass(results, "forecast-value").every((node) => node.attributes["aria-label"]), true);
    assert.equal(withClass(results, "is-unavailable").some((node) => /Unavailable/.test(node.textContent)), true);
    assert.equal(descendants(results).some((node) => node.tagName === "IMG"), false);
    assert.match(results.textContent, /<img src=x onerror=alert\(1\)>/);
    assert.doesNotMatch(results.textContent, /Device sim-farm|Simulated input|Input through|Forecast target|Forecasts are model estimates/);
    for (const card of withClass(results, "forecast-card")) {
        assert.ok(card.textContent.indexOf("Predicted values in 2 hours") < card.textContent.indexOf("Review the forecast."));
    }

    predictionState = {
        ...predictionState, status: "idle", request_id: null, input_row_id: 121,
        cooldown_seconds: 0, message: "Ready to predict.", can_predict: true,
    };
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-good", "Good", "normal")] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(predictButton.disabled, false);
    assert.match(predictionStatus.textContent, /Ready to predict/);

    predictButton.listeners.click();
    predictButton.listeners.click();
    assert.equal(predictButton.disabled, true);
    assert.equal(predictButton.textContent, "Predicting…");
    await settle();
    await settle();
    assert.equal(postCount, 1);
    assert.match(predictionStatus.textContent, /queued/i);

    predictionState = {
        ...predictionState, status: "running", request_id: "request-2",
        message: "Generating prediction and guidance.", can_predict: false,
    };
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-good", "Good", "normal")] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(predictButton.disabled, true);
    assert.equal(predictButton.textContent, "Predicting…");

    predictionState = {
        ...predictionState, status: "completed", cooldown_seconds: 8,
        message: "Prediction complete.", can_predict: false,
    };
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-good", "Good", "normal")] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(predictButton.disabled, true);
    assert.match(predictionStatus.textContent, /8 seconds/);

    predictionState = {
        ...predictionState, status: "error", cooldown_seconds: 8,
        message: "Qwen could not finish. Wait 8 seconds before predicting again.", can_predict: false,
    };
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-good", "Good", "normal")] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(predictButton.disabled, true);
    assert.match(predictionStatus.textContent, /Qwen could not finish/);
    assert.match(predictionStatus.textContent, /8 seconds/);

    predictionState = {
        ...predictionState, status: "idle", input_row_id: 122, cooldown_seconds: 0,
        message: "A new reading is ready.", can_predict: true,
    };
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-good", "Good", "normal")] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(predictButton.disabled, false);

    postResponse = {
        status: 429,
        payload: {
            ...predictionState, status: "completed", cooldown_seconds: 30,
            message: "Please wait before predicting again.", can_predict: false,
        },
    };
    predictButton.listeners.click();
    await settle();
    await settle();
    assert.equal(postCount, 2);
    assert.equal(predictButton.disabled, true);
    assert.match(predictionStatus.textContent, /30 seconds/);
    predictionState = postResponse.payload;

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
    assert.equal(results.textContent, "No predictions or guidance available yet.");
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
        assert.doesNotMatch(results.textContent, /2026-10-30|Previous forecast target/);
        if (status === "unavailable") assert.equal(withClass(results, "forecast-value").length, 0);
        assert.match(results.textContent, status === "pending" ? /Updating guidance/ : status === "error" ? /update failed/ : /Fresh readings are needed/);
        assert.equal(refreshDelay, 3000);
    }
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "pending", explanation: null,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /This panel updates automatically/);
    assert.equal(refreshDelay, 3000);
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation_status: "error", explanation: null, reason: "QWEN_TIMEOUT",
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /It took too long to respond/);
    assert.match(results.textContent, /Ollama service and forecast worker logs/);
    assert.equal(withClass(results, "forecast-value").length, 1);
    assert.equal(refreshDelay, 3000);

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
    assert.equal(refreshDelay, 3000);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation: "New completed guidance.", last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /New completed guidance/);
    assert.doesNotMatch(results.textContent, /Previous advice/);
    assert.doesNotMatch(panel.textContent, /Could not refresh/);
    assert.equal(refreshDelay, 3000);
    payloads.push({ configured: true, forecasts: [] });
    intervalCallback();
    await settle();
    await settle();
    assert.doesNotMatch(results.textContent, /New completed guidance/);
    assert.match(results.textContent, /No predictions or guidance/);

    predictionState = {
        configured: false, status: "error", request_id: null, device_id: null,
        input_row_id: null, cooldown_seconds: 0, message: "Manual prediction is unavailable.", can_predict: false,
    };
    payloads.push({ configured: true, forecasts: [] });
    intervalCallback();
    await settle();
    await settle();
    assert.equal(predictionControls.hidden, true);
    assert.equal(predictButton.disabled, true);
    assert.equal(predictionStatus.textContent, "");

    assert.equal(typeof listeners.visibilitychange, "function");
    document.hidden = true;
    const previousFetchCount = fetchCount;
    intervalCallback();
    await settle();
    assert.equal(fetchCount, previousFetchCount);
    predictionState = {
        configured: true, status: "idle", request_id: null, device_id: "sim-farm-mid",
        input_row_id: 123, cooldown_seconds: 0, message: "Ready to predict.", can_predict: true,
    };
    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning")] });
    document.hidden = false;
    listeners.visibilitychange();
    await settle();
    await settle();
    assert.equal(fetchCount, previousFetchCount + 2);
    assert.match(results.textContent, /Review the forecast/);
    assert.equal(refreshDelay, 3000);
    console.log("PASS: forecast rendering and manual prediction button states, cooldown, single-submit, polling, and text-only rendering.");
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
