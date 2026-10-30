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
        this._textContent = "";
    }
    set textContent(value) { this._textContent = String(value); this.children = []; }
    get textContent() { return this._textContent + this.children.map((child) => child.textContent).join(""); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._textContent = ""; this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
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

    const document = {
        hidden: false,
        createElement: (tag) => new Element(tag),
        getElementById: (id) => ({ "forecast-panel": panel, "forecast-results": results }[id] || null),
        addEventListener: (type, callback) => { listeners[type] = callback; },
    };
    const window = {
        setTimeout: () => 1,
        clearTimeout: () => {},
        setInterval: (callback) => { intervalCallback = callback; return 2; },
        location: { assign: () => assert.fail("Unexpected redirect") },
    };
    const response = (payload) => ({
        ok: true,
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
        fetch: async () => {
            const payload = payloads.shift();
            if (payload instanceof Error) throw payload;
            return response(payload);
        },
        window,
    });
    await settle();
    await settle();

    assert.equal(panel.attributes["aria-busy"], "false");
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
        assert.ok(card.textContent.indexOf("Predicted values in 2 hours") < card.textContent.indexOf("Reasoning AI guidance"));
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
        assert.match(results.textContent, status === "pending" ? /Updating guidance/ : status === "error" ? /update failed/ : /Historical guidance/);
    }
    payloads.push(new Error("Offline"));
    intervalCallback();
    await settle();
    assert.match(results.textContent, /Previous advice: check ventilation/);
    assert.match(panel.textContent, /Could not refresh forecasts/);

    payloads.push({ configured: true, forecasts: [forecast("sim-farm-mid", "Warning", "warning", {
        explanation: "New completed guidance.", last_guidance: savedGuidance,
    })] });
    intervalCallback();
    await settle();
    await settle();
    assert.match(results.textContent, /New completed guidance/);
    assert.doesNotMatch(results.textContent, /Previous advice/);
    assert.doesNotMatch(panel.textContent, /Could not refresh/);
    payloads.push({ configured: true, forecasts: [] });
    intervalCallback();
    await settle();
    await settle();
    assert.doesNotMatch(results.textContent, /New completed guidance/);
    assert.match(results.textContent, /No predictions or guidance/);

    assert.equal(typeof listeners.visibilitychange, "function");
    console.log("PASS: predictions before guidance, removed metadata, hidden empty/stale cards, colors, retained/replaced guidance, refresh failure, and text-only rendering.");
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
