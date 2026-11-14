// Run: node web_app/test_charts.js. No database or browser required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "static/charts.js"), "utf8");

class ClassList {
    constructor(el) {
        this.el = el;
    }
    _set() {
        return new Set(this.el.className.split(/\s+/).filter(Boolean));
    }
    _write(set) {
        this.el.className = Array.from(set).join(" ");
    }
    contains(name) {
        return this._set().has(name);
    }
    add(...names) {
        const set = this._set();
        names.forEach((n) => set.add(n));
        this._write(set);
    }
    remove(...names) {
        const set = this._set();
        names.forEach((n) => set.delete(n));
        this._write(set);
    }
    toggle(name, force) {
        const set = this._set();
        const next = force === undefined ? !set.has(name) : Boolean(force);
        if (next) set.add(name);
        else set.delete(name);
        this._write(set);
        return next;
    }
}

class StyleMap {
    constructor() {
        this.props = {};
    }
    setProperty(name, value) {
        this.props[name] = String(value);
    }
    getPropertyValue(name) {
        return this.props[name] || "";
    }
}

class Element {
    constructor(tag = "div") {
        this.tagName = tag.toUpperCase();
        this.id = "";
        this.children = [];
        this.parentNode = null;
        this.attributes = {};
        this.className = "";
        this.classList = new ClassList(this);
        this.dataset = {};
        this.style = new StyleMap();
        this.hidden = false;
        this.listeners = {};
        this._textContent = "";
    }
    set textContent(value) {
        this._textContent = String(value);
        this.children = [];
    }
    get textContent() {
        return this._textContent + this.children.map((child) => child.textContent).join("");
    }
    _attach(child) {
        if (child.parentNode && child.parentNode !== this) {
            child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
        } else if (child.parentNode === this) {
            this.children = this.children.filter((c) => c !== child);
        }
        child.parentNode = this;
        this.children.push(child);
        return child;
    }
    append(...children) {
        children.forEach((child) => this._attach(child));
    }
    appendChild(child) {
        return this._attach(child);
    }
    replaceChildren(...children) {
        this._textContent = "";
        this.children.forEach((child) => { child.parentNode = null; });
        this.children = [];
        children.forEach((child) => this._attach(child));
    }
    setAttribute(name, value) {
        const str = String(value);
        this.attributes[name] = str;
        if (name === "class") this.className = str;
        if (name === "id") this.id = str;
        if (name.startsWith("data-")) {
            const camel = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
            this.dataset[camel] = str;
        }
    }
    getAttribute(name) {
        return this.attributes[name];
    }
    addEventListener(type, callback) {
        this.listeners[type] = callback;
    }
    matches(selector) {
        return selector.split(",").some((part) => {
            const s = part.trim();
            if (s.startsWith(".")) return this.classList.contains(s.slice(1));
            if (s === "svg.sensor-trend") return this.tagName === "SVG" && this.classList.contains("sensor-trend");
            if (s === "ul.chart-series-legend") return this.tagName === "UL" && this.classList.contains("chart-series-legend");
            if (s === "polyline.trend-line") return this.tagName === "POLYLINE" && this.classList.contains("trend-line");
            if (s === ".chart-heading-meta .severity-pill") return this.classList.contains("severity-pill");
            if (s === ".trend-chart-wrap .chart-empty-state") return this.classList.contains("chart-empty-state");
            if (s === "[role='button']") return this.attributes.role === "button";
            if (/^[a-z]+$/i.test(s)) return this.tagName === s.toUpperCase();
            return false;
        });
    }
    closest(selector) {
        let cur = this;
        while (cur) {
            if (cur.matches(selector)) return cur;
            cur = cur.parentNode;
        }
        return null;
    }
    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }
    querySelectorAll(selector) {
        const all = [];
        const walk = (node) => {
            for (const child of node.children) {
                if (child.matches(selector)) all.push(child);
                walk(child);
            }
        };
        walk(this);
        return all;
    }
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function buildChartPanel(id) {
    const panel = new Element("section");
    panel.id = id;
    panel.className = "dashboard-panel sensor-chart-panel";

    const headingMeta = new Element("div");
    headingMeta.className = "chart-heading-meta";
    const pill = new Element("span");
    pill.className = "severity-pill is-normal";
    headingMeta.append(pill);

    const wrap = new Element("div");
    wrap.className = "trend-chart-wrap";
    const svg = new Element("svg");
    svg.className = "sensor-trend";
    const legend = new Element("ul");
    legend.className = "chart-series-legend";
    const empty = new Element("div");
    empty.className = "chart-empty-state";
    wrap.append(svg, legend, empty);

    panel.append(headingMeta, wrap);
    return panel;
}

async function run() {
    const grid = new Element("div");
    grid.className = "sensor-charts-grid";
    grid.dataset.chartsUrl = "/api/charts";

    const pmPanel = buildChartPanel("chart-pm1_ugm3");
    const nh3Panel = buildChartPanel("chart-mq137_ppm");
    grid.append(nh3Panel, pmPanel);

    let fetchCount = 0;
    const makePayload = (pm1Latest) => ({
        configured: true,
        has_forecast: true,
        recorded_start: "2026-11-14T00:00:00Z",
        recorded_end: "2026-11-14T01:00:00Z",
        forecast_end: "2026-11-14T03:00:00Z",
        ticks: [[60, "08:00 AM"], [760, "11:00 AM"]],
        charts: [
            {
                chart_id: "chart-mq137_ppm",
                column: "mq137_ppm",
                title: "Ammonia",
                unit: "ppm",
                severity: "normal",
                badge: "NORMAL",
                has_points: true,
                plot_right: 760,
                view_box: "-18 14 798 252",
                ticks: [[60, "08:00 AM"], [760, "11:00 AM"]],
                y_ticks: [[232, "0"], [24, "10"]],
                threshold_lines: [],
                series_list: [{
                    column: "mq137_ppm",
                    label: "Ammonia",
                    unit: "ppm",
                    color: "#38bdf8",
                    segments: ["60,120 280,118"],
                    forecast_segments: ["280,118 760,115"],
                    hover_points: [
                        [60, 120, "08:00 AM", 5.2, "recorded"],
                        [280, 118, "09:00 AM", 5.8, "recorded"],
                        [760, 115, "11:00 AM", 6.4, "forecast"],
                    ],
                    latest_value: 5.8,
                }],
            },
            {
                chart_id: "chart-pm1_ugm3",
                column: "pm1_ugm3",
                title: "Particulate matter",
                unit: "ug/m3",
                severity: "normal",
                badge: "NORMAL",
                has_points: true,
                plot_right: 1000,
                view_box: "-18 14 1038 252",
                ticks: [[60, "08:00 AM"], [1000, "11:00 AM"]],
                y_ticks: [[232, "1"], [24, "100"]],
                threshold_lines: [],
                series_list: [
                    {
                        column: "pm1_ugm3",
                        label: "PM 1.0",
                        unit: "ug/m3",
                        color: "#22d3ee",
                        segments: ["60,180 350,190"],
                        forecast_segments: ["350,190 1000,220"],
                        hover_points: [
                            [60, 180, "08:00 AM", 6.1, "recorded"],
                            [350, 190, "09:00 AM", pm1Latest, "recorded"],
                            [1000, 220, "11:00 AM", 3.2, "forecast"],
                        ],
                        latest_value: pm1Latest,
                    },
                    {
                        column: "pm25_ugm3",
                        label: "PM 2.5",
                        unit: "ug/m3",
                        color: "#818cf8",
                        segments: ["60,140 350,150"],
                        forecast_segments: ["350,150 1000,175"],
                        hover_points: [
                            [60, 140, "08:00 AM", 19.5, "recorded"],
                            [350, 150, "09:00 AM", 17, "recorded"],
                            [1000, 175, "11:00 AM", 11.4, "forecast"],
                        ],
                        latest_value: 17,
                    },
                    {
                        column: "pm10_ugm3",
                        label: "PM 10",
                        unit: "ug/m3",
                        color: "#c084fc",
                        segments: ["60,140 350,150"],
                        forecast_segments: ["350,150 1000,175"],
                        hover_points: [
                            [60, 140, "08:00 AM", 24.0, "recorded"],
                            [350, 150, "09:00 AM", 22.1, "recorded"],
                            [1000, 175, "11:00 AM", 14.8, "forecast"],
                        ],
                        latest_value: 17,
                    },
                ],
            },
        ],
    });

    let scheduledRefresh;
    let scrollTopCount = 0;
    const topMetrics = {
        id: "dashboard-top-metrics",
        scrollIntoView: (opts) => {
            assert.equal(opts.behavior, "smooth");
            assert.equal(opts.block, "start");
            scrollTopCount += 1;
        },
    };
    const forecastPanel = new Element("section");
    forecastPanel.id = "forecast-panel";
    forecastPanel.className = "dashboard-panel ai-panel";
    const forecastLink = new Element("a");
    forecastLink.setAttribute("href", "#chart-mq137_ppm");
    forecastPanel.append(forecastLink);
    grid.append(forecastPanel);

    const documentMock = {
        hidden: false,
        querySelector: (selector) => (selector === ".sensor-charts-grid[data-charts-url]" ? grid : null),
        getElementById: (id) => (
            id === "dashboard-top-metrics"
                ? topMetrics
                : (id === "chart-pm1_ugm3" ? pmPanel : (id === "chart-mq137_ppm" ? nh3Panel : null))
        ),
        createElement: (tag) => new Element(tag),
        createElementNS: (_, tag) => new Element(tag),
        addEventListener: () => {},
    };

    const windowMock = {
        location: { origin: "http://localhost:5000", search: "" },
        setTimeout: (cb) => { scheduledRefresh = cb; return 1; },
        clearTimeout: () => {},
    };

    vm.runInNewContext(source, {
        document: documentMock,
        window: windowMock,
        URL,
        URLSearchParams,
        AbortController,
        fetch: async () => {
            fetchCount += 1;
            return {
                ok: true,
                redirected: false,
                url: "http://localhost:5000/api/charts",
                json: async () => makePayload(fetchCount === 1 ? 5 : 6),
            };
        },
    });
    await settle();

    const pmSvg = pmPanel.querySelector("svg.sensor-trend");
    const nh3Svg = nh3Panel.querySelector("svg.sensor-trend");
    assert.equal(pmSvg.getAttribute("viewBox"), "-18 14 1038 252");
    assert.equal(nh3Svg.getAttribute("viewBox"), "-18 14 798 252");

    const axis = pmSvg.children.find((node) => node.classList.contains("trend-axis"));
    assert.equal(axis.getAttribute("d"), "M60 24V232H1000");

    const toggles = pmPanel.querySelectorAll(".chart-series-toggle");
    assert.equal(toggles.length, 6);
    const pm25Toggle = toggles.find((btn) => btn.dataset.seriesCol === "pm25_ugm3" && btn.dataset.seriesKind === "recorded");
    assert.ok(pm25Toggle);

    // Click PM 2.5 recorded button: promotes matching polyline above overlapping PM 10 and dims others.
    grid.listeners.click({ target: pm25Toggle });
    assert.equal(pm25Toggle.getAttribute("aria-pressed"), "true");
    assert.ok(pm25Toggle.classList.contains("is-active"));
    assert.ok(pmSvg.classList.contains("has-series-focus"));

    let pmLines = pmSvg.querySelectorAll("polyline.trend-line");
    assert.equal(pmLines.length, 6);
    assert.ok(pmLines[pmLines.length - 1].classList.contains("is-highlighted"));
    assert.equal(pmLines[pmLines.length - 1].dataset.seriesCol, "pm25_ugm3");
    assert.equal(pmLines[pmLines.length - 1].dataset.seriesKind, "recorded");
    assert.equal(pmLines.filter((l) => l.classList.contains("is-dimmed")).length, 5);

    // Subsequent /api/charts update keeps the active PM 2.5 highlight intact.
    await scheduledRefresh();
    await settle();
    const updatedPm25Toggle = pmPanel.querySelectorAll(".chart-series-toggle")
        .find((btn) => btn.dataset.seriesCol === "pm25_ugm3" && btn.dataset.seriesKind === "recorded");
    assert.equal(updatedPm25Toggle.getAttribute("aria-pressed"), "true");
    assert.ok(updatedPm25Toggle.classList.contains("is-active"));
    pmLines = pmSvg.querySelectorAll("polyline.trend-line");
    assert.equal(pmLines[pmLines.length - 1].dataset.seriesCol, "pm25_ugm3");
    assert.ok(pmLines[pmLines.length - 1].classList.contains("is-highlighted"));

    // Clicking the active toggle again resets highlights and restores ascending data-series-order.
    grid.listeners.click({ target: updatedPm25Toggle });
    assert.equal(updatedPm25Toggle.getAttribute("aria-pressed"), "false");
    assert.ok(!pmSvg.classList.contains("has-series-focus"));
    pmLines = pmSvg.querySelectorAll("polyline.trend-line");
    assert.deepEqual(pmLines.map((l) => Number(l.dataset.seriesOrder)), [0, 1, 2, 3, 4, 5]);
    assert.equal(pmLines.filter((l) => l.classList.contains("is-highlighted") || l.classList.contains("is-dimmed")).length, 0);

    // Interactive hover scrubbing across multi-series Particulate matter + forecast + highlight dimming.
    const pmWrap = pmPanel.querySelector(".trend-chart-wrap");
    pmWrap.clientWidth = 1038;
    pmWrap.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1038, height: 320, bottom: 320 });
    pmSvg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1038, height: 252, bottom: 252 });

    // Hover near x=350 (svgX = -18 + 368 = 350) -> snaps all 3 PM series at 09:00 AM.
    pmSvg.listeners.pointermove({ pointerType: "mouse", clientX: 368, clientY: 150 });
    const pmCrosshair = pmSvg.querySelector(".trend-crosshair");
    assert.ok(pmCrosshair);
    assert.equal(pmCrosshair.getAttribute("x1"), "350");
    assert.equal(pmCrosshair.getAttribute("y1"), "24");
    assert.equal(pmCrosshair.getAttribute("y2"), "232");
    const pmDots = pmSvg.querySelectorAll(".trend-hover-dot");
    assert.equal(pmDots.length, 3);
    const pmTooltip = pmWrap.querySelector(".chart-hover-tooltip");
    assert.ok(pmTooltip && !pmTooltip.hidden);
    assert.ok(pmTooltip.textContent.includes("09:00 AM"));
    assert.ok(pmTooltip.textContent.includes("PM 1.0 : 6"));
    assert.ok(pmTooltip.textContent.includes("PM 2.5 : 17"));
    assert.ok(pmTooltip.textContent.includes("PM 10 : 22.1"));
    const leftSideLeft = parseFloat(pmTooltip.style.left);
    assert.ok(leftSideLeft > 368);

    // Scrubbing near right edge (x=1000 -> clientX=1018) snaps forecast points and flips tooltip left.
    pmSvg.listeners.pointermove({ pointerType: "mouse", clientX: 1018, clientY: 180 });
    assert.ok(pmTooltip.textContent.includes("11:00 AM"));
    assert.ok(pmTooltip.textContent.includes("PM 1.0 (forecast) : 3.2"));
    const rightSideLeft = parseFloat(pmTooltip.style.left);
    assert.ok(rightSideLeft < 1018);

    // With PM 2.5 highlighted, touch-drag dims PM 1.0 & PM 10 dots/rows and promotes PM 2.5 dot last.
    grid.listeners.click({ target: updatedPm25Toggle });
    pmSvg.listeners.pointerdown({ pointerType: "touch", pointerId: 7, clientX: 368, clientY: 150 });
    const focusedDots = pmSvg.querySelectorAll(".trend-hover-dot");
    assert.equal(focusedDots.length, 3);
    assert.ok(focusedDots[2].classList.contains("is-highlighted"));
    assert.equal(focusedDots.filter((d) => d.classList.contains("is-dimmed")).length, 2);
    assert.equal(pmTooltip.querySelectorAll(".chart-hover-row").filter((r) => r.classList.contains("is-dimmed")).length, 2);

    // Pointerleave & pointercancel hide tooltip and clear overlay.
    pmSvg.listeners.pointercancel({ pointerType: "touch" });
    assert.equal(pmTooltip.hidden, true);
    assert.equal(pmSvg.querySelectorAll(".trend-hover-dot").length, 0);

    pmSvg.listeners.pointermove({ pointerType: "mouse", clientX: 78, clientY: 160 });
    assert.equal(pmTooltip.hidden, false);
    pmSvg.listeners.pointerleave({ pointerType: "mouse" });
    assert.equal(pmTooltip.hidden, true);
    assert.equal(pmSvg.querySelectorAll(".trend-crosshair").length, 0);

    // Pressing an empty part of a chart panel, forecast card, or graph jumps back to the top squares.
    const beforeEmptyClicks = scrollTopCount;
    grid.listeners.pointerdown({ button: 0, clientX: 20, clientY: 20 });
    grid.listeners.click({ target: pmWrap });
    assert.equal(scrollTopCount, beforeEmptyClicks + 1);

    grid.listeners.pointerdown({ button: 0, clientX: 120, clientY: 120 });
    grid.listeners.click({ target: pmSvg });
    assert.equal(scrollTopCount, beforeEmptyClicks + 2);

    grid.listeners.pointerdown({ button: 0, clientX: 10, clientY: 10 });
    grid.listeners.click({ target: forecastPanel });
    assert.equal(scrollTopCount, beforeEmptyClicks + 3);

    // Interactive children (legend buttons, forecast tile links) and dragged scrubs do NOT jump.
    grid.listeners.click({ target: updatedPm25Toggle });
    grid.listeners.click({ target: forecastLink });
    grid.listeners.pointerdown({ button: 0, clientX: 100, clientY: 100 });
    grid.listeners.pointermove({ clientX: 140, clientY: 102 });
    grid.listeners.click({ target: pmSvg });
    assert.equal(scrollTopCount, beforeEmptyClicks + 3);

    console.log("test_charts.js passed");
}

run().catch((err) => {
    console.error(err);
    process.exit(1);
});

