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

    const updateChartPanel = (chart, ticks, payload) => {
        const panel = document.getElementById(chart.chart_id);
        if (!panel) return;

        const badge = panel.querySelector(".chart-heading-meta .severity-pill");
        if (badge) {
            badge.className = `severity-pill is-${safeSeverity(chart.severity)}`;
            badge.textContent = chart.badge || "NO DATA";
        }

        const svg = panel.querySelector("svg.sensor-trend");
        if (svg) {
            const title = svg.querySelector("title");
            const descId = title?.id || `chart-desc-${chart.column}`;
            const nodes = [];
            const summary = payload.has_forecast
                ? `${chart.title} recorded readings and 2-hour forecast through ${payload.forecast_end || ""} UTC in ${chart.unit}.`
                : `${chart.title} recorded readings through ${payload.recorded_end || ""} UTC in ${chart.unit}.`;
            nodes.push(svgEl("title", { id: descId }, summary));

            (chart.y_ticks || []).forEach(([y, tickLabel]) => {
                nodes.push(svgEl("path", { class: "trend-grid", d: `M60 ${y}H760` }));
                nodes.push(svgEl("line", { class: "trend-axis-tick", x1: 55, y1: y, x2: 60, y2: y }));
                nodes.push(svgEl("text", { class: "trend-tick-label", x: 51, y: Number(y) + 4, "text-anchor": "end" }, tickLabel));
            });

            (ticks || []).forEach(([x, label], idx, arr) => {
                const xNum = Number(x);
                nodes.push(svgEl("path", { class: "trend-grid", d: `M${x} 24V232` }));
                nodes.push(svgEl("line", { class: "trend-axis-tick", x1: x, y1: 232, x2: x, y2: 237 }));
                const isFirst = idx === 0 && xNum <= 65;
                const isLast = idx === arr.length - 1 && xNum >= 755;
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
                    x1: 60, y1: tline.y, x2: 760, y2: tline.y,
                }));
            });

            nodes.push(svgEl("path", { class: "trend-axis", d: "M60 24V232H760" }));
            if (chart.unit) {
                nodes.push(svgEl("text", {
                    class: "trend-axis-unit",
                    x: -4,
                    y: 128,
                    "text-anchor": "middle",
                    transform: "rotate(-90 -4 128)",
                }, chart.unit));
            }

            (chart.series_list || []).forEach((series) => {
                const color = safeColor(series.color);
                (series.segments || []).forEach((points) => {
                    const line = svgEl("polyline", { class: "trend-line", points });
                    line.style.stroke = color;
                    line.style.filter = `drop-shadow(0 2px 5px ${color}55)`;
                    nodes.push(line);
                });
                (series.forecast_segments || []).forEach((points) => {
                    const line = svgEl("polyline", { class: "trend-line is-forecast", points });
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
                const item = document.createElement("li");
                item.className = "chart-series-item";

                const swatch = document.createElement("span");
                swatch.className = "chart-series-swatch";
                swatch.style.setProperty("--series-color", color);
                swatch.setAttribute("aria-hidden", "true");

                const name = document.createElement("span");
                name.className = "chart-series-name";
                name.textContent = series.label;

                item.append(swatch, name);
                if (series.latest_value !== null && series.latest_value !== undefined) {
                    const strong = document.createElement("strong");
                    strong.className = "chart-series-latest";
                    strong.textContent = `${formatReading(series.latest_value)} `;
                    const small = document.createElement("small");
                    small.textContent = series.unit || "";
                    strong.append(small);
                    item.append(strong);
                }
                items.push(item);

                if (series.forecast_segments && series.forecast_segments.length) {
                    const forecastItem = document.createElement("li");
                    forecastItem.className = "chart-series-item is-forecast";

                    const fSwatch = document.createElement("span");
                    fSwatch.className = "chart-series-swatch is-forecast";
                    fSwatch.style.setProperty("--series-color", color);
                    fSwatch.setAttribute("aria-hidden", "true");

                    const fName = document.createElement("span");
                    fName.className = "chart-series-name";
                    fName.textContent = `${series.label} forecast`;

                    forecastItem.append(fSwatch, fName);
                    items.push(forecastItem);
                }
            });
            legend.replaceChildren(...items);
        }

        const emptyState = panel.querySelector(".trend-chart-wrap .chart-empty-state");
        if (emptyState) {
            emptyState.hidden = Boolean(chart.has_points);
        }
    };

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

