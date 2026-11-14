(() => {
    const SVG_NS = "http://www.w3.org/2000/svg";
    const grid = document.querySelector(".sensor-charts-grid[data-charts-url]");
    if (!grid) return;

    const chartUrl = () => {
        const base = new URL(grid.dataset.chartsUrl, window.location.origin);
        const sensor = new URLSearchParams(window.location.search).get("sensor");
        if (sensor) base.searchParams.set("sensor", sensor);
        return base.toString();
    };

    const svgEl = (tag, attrs = {}, text = null) => {
        const el = document.createElementNS(SVG_NS, tag);
        Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
        if (text !== null) el.textContent = String(text);
        return el;
    };

    const safeColor = (color) => /^#[0-9a-fA-F]{6}$/.test(String(color || "")) ? color : "#38bdf8";
    const safeSeverity = (value) => ["normal", "warning", "critical"].includes(value) ? value : "unavailable";
    const formatReading = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(2) : "--";
    const activeHighlights = new Map();

    const applySeriesHighlight = (panel, highlight) => {
        if (!panel) return;
        const buttons = Array.from(panel.querySelectorAll(".chart-series-toggle"));
        let active = highlight;
        if (active && !buttons.some((btn) => btn.dataset.seriesCol === active.col && btn.dataset.seriesKind === active.kind)) {
            activeHighlights.delete(panel.id);
            active = null;
        }
        buttons.forEach((btn) => {
            const matches = Boolean(active)
                && btn.dataset.seriesCol === active.col
                && btn.dataset.seriesKind === active.kind;
            btn.classList.toggle("is-active", matches);
            btn.classList.toggle("is-dimmed", Boolean(active) && !matches);
            btn.setAttribute("aria-pressed", matches ? "true" : "false");
        });

        const svg = panel.querySelector("svg.sensor-trend");
        if (svg) {
            svg.classList.toggle("has-series-focus", Boolean(active));
            const lines = Array.from(svg.querySelectorAll("polyline.trend-line"));
            lines.sort((a, b) => Number(a.dataset.seriesOrder || 0) - Number(b.dataset.seriesOrder || 0));
            const promoted = [];
            lines.forEach((line) => {
                const matches = Boolean(active)
                    && line.dataset.seriesCol === active.col
                    && line.dataset.seriesKind === active.kind;
                line.classList.toggle("is-highlighted", matches);
                line.classList.toggle("is-dimmed", Boolean(active) && !matches);
                svg.appendChild(line);
                if (matches) promoted.push(line);
            });
            promoted.forEach((line) => svg.appendChild(line));
        }
    };

    const createLegendToggle = (series, color, kind) => {
        const entry = document.createElement("li");
        entry.className = "chart-series-entry";

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = kind === "forecast"
            ? "chart-series-item chart-series-toggle is-forecast"
            : "chart-series-item chart-series-toggle";
        btn.dataset.seriesCol = String(series.column || "");
        btn.dataset.seriesKind = kind;
        btn.setAttribute("aria-pressed", "false");
        btn.style.setProperty("--series-color", color);

        const swatch = document.createElement("span");
        swatch.className = kind === "forecast" ? "chart-series-swatch is-forecast" : "chart-series-swatch";
        swatch.style.setProperty("--series-color", color);
        swatch.setAttribute("aria-hidden", "true");

        const name = document.createElement("span");
        name.className = "chart-series-name";
        name.textContent = kind === "forecast" ? `${series.label} forecast` : series.label;

        btn.append(swatch, name);
        if (kind === "recorded" && series.latest_value !== null && series.latest_value !== undefined) {
            const strong = document.createElement("strong");
            strong.className = "chart-series-latest";
            strong.textContent = `${formatReading(series.latest_value)} `;
            const small = document.createElement("small");
            small.textContent = series.unit || "";
            strong.append(small);
            btn.append(strong);
        }
        entry.append(btn);
        return entry;
    };

    const updateChartPanel = (chart, ticks, payload) => {
        const panel = document.getElementById(chart.chart_id);
        if (!panel) return;

        const badge = panel.querySelector(".chart-heading-meta .severity-pill");
        if (badge) {
            badge.className = `severity-pill is-${safeSeverity(chart.severity)}`;
            badge.textContent = chart.badge || "NO DATA";
        }

        const plotRight = Number(chart.plot_right) || 760;
        const chartTicks = Array.isArray(chart.ticks) ? chart.ticks : (ticks || []);

        const svg = panel.querySelector("svg.sensor-trend");
        if (svg) {
            svg.setAttribute("viewBox", chart.view_box || "-18 14 798 252");
            const title = svg.querySelector("title");
            const descId = title?.id || `chart-desc-${chart.column}`;
            const nodes = [];
            const summary = payload.has_forecast
                ? `${chart.title} recorded readings and 2-hour forecast through ${payload.forecast_end || ""} UTC in ${chart.unit}.`
                : `${chart.title} recorded readings through ${payload.recorded_end || ""} UTC in ${chart.unit}.`;
            nodes.push(svgEl("title", { id: descId }, summary));

            (chart.y_ticks || []).forEach(([y, tickLabel]) => {
                nodes.push(svgEl("path", { class: "trend-grid", d: `M60 ${y}H${plotRight}` }));
                nodes.push(svgEl("line", { class: "trend-axis-tick", x1: 55, y1: y, x2: 60, y2: y }));
                nodes.push(svgEl("text", { class: "trend-tick-label", x: 51, y: Number(y) + 4, "text-anchor": "end" }, tickLabel));
            });

            chartTicks.forEach(([x, label], idx, arr) => {
                const xNum = Number(x);
                nodes.push(svgEl("path", { class: "trend-grid", d: `M${x} 24V232` }));
                nodes.push(svgEl("line", { class: "trend-axis-tick", x1: x, y1: 232, x2: x, y2: 237 }));
                const isFirst = idx === 0 && xNum <= 65;
                const isLast = idx === arr.length - 1 && xNum >= plotRight - 5;
                nodes.push(svgEl("text", {
                    class: "trend-tick-label",
                    x: isFirst ? 62 : x,
                    y: 254,
                    "text-anchor": isFirst ? "start" : (isLast ? "end" : "middle"),
                }, label));
            });

            (chart.threshold_lines || []).forEach((tline) => {
                const kind = safeSeverity(tline.kind);
                nodes.push(svgEl("line", {
                    class: `trend-threshold is-${kind}`,
                    x1: 60, y1: tline.y, x2: plotRight, y2: tline.y,
                }));
            });

            nodes.push(svgEl("path", { class: "trend-axis", d: `M60 24V232H${plotRight}` }));
            if (chart.unit) {
                nodes.push(svgEl("text", {
                    class: "trend-axis-unit",
                    x: -4,
                    y: 128,
                    "text-anchor": "middle",
                    transform: "rotate(-90 -4 128)",
                }, chart.unit));
            }

            let lineOrder = 0;
            (chart.series_list || []).forEach((series) => {
                const color = safeColor(series.color);
                const col = String(series.column || "");
                (series.segments || []).forEach((points) => {
                    const line = svgEl("polyline", {
                        class: "trend-line",
                        points,
                        "data-series-col": col,
                        "data-series-kind": "recorded",
                        "data-series-order": lineOrder,
                    });
                    lineOrder += 1;
                    line.style.setProperty("--series-color", color);
                    line.style.stroke = color;
                    line.style.filter = `drop-shadow(0 2px 5px ${color}55)`;
                    nodes.push(line);
                });
                (series.forecast_segments || []).forEach((points) => {
                    const line = svgEl("polyline", {
                        class: "trend-line is-forecast",
                        points,
                        "data-series-col": col,
                        "data-series-kind": "forecast",
                        "data-series-order": lineOrder,
                    });
                    lineOrder += 1;
                    line.style.setProperty("--series-color", color);
                    line.style.stroke = color;
                    line.style.filter = `drop-shadow(0 2px 5px ${color}44)`;
                    nodes.push(line);
                });
            });

            svg.replaceChildren(...nodes);
        }

        const legend = panel.querySelector("ul.chart-series-legend");
        if (legend) {
            const items = [];
            (chart.series_list || []).forEach((series) => {
                const color = safeColor(series.color);
                items.push(createLegendToggle(series, color, "recorded"));
                if (series.forecast_segments && series.forecast_segments.length) {
                    items.push(createLegendToggle(series, color, "forecast"));
                }
            });
            legend.replaceChildren(...items);
        }

        const emptyState = panel.querySelector(".trend-chart-wrap .chart-empty-state");
        if (emptyState) {
            emptyState.hidden = Boolean(chart.has_points);
        }

        applySeriesHighlight(panel, activeHighlights.get(panel.id) || null);
    };

    grid.addEventListener("click", (event) => {
        const btn = event.target?.closest?.(".chart-series-toggle");
        if (!btn) return;
        const panel = btn.closest(".sensor-chart-panel");
        if (!panel || !panel.id) return;
        const col = btn.dataset.seriesCol || "";
        const kind = btn.dataset.seriesKind || "recorded";
        const current = activeHighlights.get(panel.id);
        if (current && current.col === col && current.kind === kind) {
            activeHighlights.delete(panel.id);
            applySeriesHighlight(panel, null);
        } else {
            const next = { col, kind };
            activeHighlights.set(panel.id, next);
            applySeriesHighlight(panel, next);
        }
    });

    let loading = false;
    let renderedFingerprint = null;
    let refreshTimer;

    const notifyUpdated = (payload) => {
        if (typeof document.dispatchEvent !== "function" || typeof CustomEvent !== "function") return;
        document.dispatchEvent(new CustomEvent("charts:updated", {
            detail: {
                hasForecast: Boolean(payload.has_forecast),
                forecastEnd: payload.forecast_end || null,
            },
        }));
    };

    const scheduleRefresh = (delay) => {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(refresh, delay);
    };

    const refresh = async () => {
        if (loading || document.hidden) return;
        window.clearTimeout(refreshTimer);
        loading = true;
        let nextDelay = 20000;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(chartUrl(), {
                credentials: "same-origin",
                headers: { Accept: "application/json" },
                cache: "no-store",
                signal: controller.signal,
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                window.location.assign(response.url);
                window.clearTimeout(timeout);
                loading = false;
                return;
            }
            if (!response.ok) throw new Error(`Chart refresh failed: ${response.status}`);
            const payload = await response.json();
            if (payload.configured && Array.isArray(payload.charts)) {
                const fingerprint = JSON.stringify(payload);
                if (fingerprint !== renderedFingerprint) {
                    const hadRendered = renderedFingerprint !== null;
                    payload.charts.forEach((chart) => updateChartPanel(chart, payload.ticks, payload));
                    renderedFingerprint = fingerprint;
                    if (hadRendered) notifyUpdated(payload);
                }
            }
        } catch (error) {
            nextDelay = 15000;
        } finally {
            window.clearTimeout(timeout);
            loading = false;
            scheduleRefresh(nextDelay);
        }
    };

    refresh();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();

