(() => {
    const root = document.querySelector("#dashboard-app");
    const chartCanvas = document.querySelector("#dashboard-chart");
    const dataNode = document.querySelector("#dashboard-initial-data");
    const configNode = document.querySelector("#dashboard-metric-config");
    if (!root || !chartCanvas || !dataNode || !configNode) return;

    let data = JSON.parse(dataNode.textContent);
    const metricConfig = JSON.parse(configNode.textContent);
    const rangeLabels = { "1h": "Last 1 hour", "6h": "Last 6 hours", "24h": "Last 24 hours", "7d": "Last 7 days" };
    let selectedMetric = "nh3_ppm";
    let chart = null;
    let requestController = null;
    let requestGeneration = 0;
    let lastSuccessAt = Date.now();

    const byId = (id) => document.getElementById(id);
    const setText = (selector, value) => {
        const element = document.querySelector(selector);
        if (element) element.textContent = value;
    };
    const formatNumber = (value, key, maximum = null) => {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
        const precision = maximum === null ? (metricConfig[key]?.precision ?? 1) : maximum;
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: precision }).format(Number(value));
    };
    const formatTimestamp = (value) => value ? `${value.slice(0, 19).replace("T", " ")} UTC` : "No reading yet";
    const statusLabel = (status) => ({
        attention: "Attention",
        within_range: "Within range",
        unavailable: "Unavailable",
        unclassified: "Monitoring",
    }[status] || "Monitoring");
    const statusClass = (status) => ({
        attention: "is-attention",
        within_range: "is-within-range",
        unavailable: "is-unavailable",
        unclassified: "is-unclassified",
        current: "is-current",
        delayed: "is-delayed",
    }[status] || "is-unavailable");

    const renderFreshness = () => {
        const latest = data.latest || {};
        const status = latest.connection_status || "unavailable";
        const dot = byId("dashboard-freshness-dot");
        if (dot) dot.className = `freshness-dot ${statusClass(status)}`;
        setText("#dashboard-freshness-label", status === "current" ? "Current" : status === "delayed" ? "Delayed" : "Unavailable");
        setText("#dashboard-last-reading", formatTimestamp(latest.recorded_at));
    };

    const renderLatest = () => {
        const metrics = data.latest?.metrics || {};
        Object.keys(metricConfig).forEach((key) => {
            const current = metrics[key] || {};
            setText(`[data-dashboard-value="${key}"]`, formatNumber(current.value, key));
            const statusElement = document.querySelector(`[data-dashboard-status="${key}"]`);
            if (statusElement) {
                statusElement.textContent = statusLabel(current.status);
                statusElement.className = `metric-card-status ${statusClass(current.status)}`;
            }
        });

        const overall = data.latest?.overall_status || "unavailable";
        const overallElement = byId("overall-assessment");
        if (overallElement) {
            overallElement.className = `assessment-status ${statusClass(overall)}`;
            overallElement.querySelector("strong").textContent = ({
                attention: "Attention required",
                within_range: "Within configured range",
                unclassified: "Unclassified",
                unavailable: "Unavailable",
            }[overall] || "Unavailable");
        }
        setText("#overall-assessment-copy", ({
            attention: "The latest NH3 reading is above the configured 25 ppm threshold.",
            within_range: "The latest NH3 reading is within the configured 25 ppm threshold.",
            unclassified: "A configured threshold is not available for the current reading.",
            unavailable: "No current reading is available.",
        }[overall] || "No current reading is available."));
    };

    const renderSummary = () => {
        const summary = data.summary?.[selectedMetric] || {};
        const config = metricConfig[selectedMetric] || {};
        setText('[data-summary-field="average"]', formatNumber(summary.average, selectedMetric, config.precision));
        setText('[data-summary-field="stddev"]', formatNumber(summary.stddev, selectedMetric, config.precision));
        const range = summary.min === null || summary.min === undefined || summary.max === null || summary.max === undefined
            ? "—"
            : `${formatNumber(summary.min, selectedMetric)}–${formatNumber(summary.max, selectedMetric)}`;
        setText('[data-summary-field="range"]', range);
        const exceedance = config.threshold === null || config.threshold === undefined
            ? "Not configured"
            : summary.exceedance_count === null || summary.exceedance_count === undefined
                ? "—"
                : `${summary.exceedance_count} (${formatNumber(summary.exceedance_percent, selectedMetric, 1)}%)`;
        setText('[data-summary-field="exceedance"]', exceedance);
    };

    const renderAlerts = () => {
        const container = byId("assessment-events");
        if (!container) return;
        container.replaceChildren();
        if (!data.alerts?.length) {
            const empty = document.createElement("div");
            empty.className = "assessment-empty";
            const title = document.createElement("strong");
            title.textContent = "No threshold exceedances";
            const copy = document.createElement("span");
            copy.textContent = "No NH3 reading exceeded 25 ppm during this period.";
            empty.append(title, copy);
            container.append(empty);
            setText("#assessment-recommendation", "No rule-based action is currently required.");
            return;
        }

        const list = document.createElement("ol");
        list.className = "assessment-event-list";
        data.alerts.forEach((event) => {
            const item = document.createElement("li");
            item.className = "assessment-event";
            const copy = document.createElement("div");
            const title = document.createElement("strong");
            title.textContent = `${event.label} above ${event.threshold} ${event.unit}`;
            const detail = document.createElement("span");
            detail.textContent = `${formatNumber(event.value, event.metric)} ${event.unit} · ${formatTimestamp(event.recorded_at)}`;
            copy.append(title, detail);
            const severity = document.createElement("small");
            severity.textContent = "Attention";
            item.append(copy, severity);
            list.append(item);
        });
        container.append(list);
        setText("#assessment-recommendation", `Recommendation: ${data.alerts[0].recommendation}`);
    };

    const chartTick = (value, index, ticks) => {
        const labels = data.series?.timestamps || [];
        const timestamp = labels[index];
        if (!timestamp || (ticks.length > 1 && index % Math.ceil(labels.length / 6) !== 0 && index !== labels.length - 1)) return "";
        return timestamp.slice(5, 16).replace("T", " ");
    };

    const chartOptions = () => ({
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        normalized: true,
        interaction: { intersect: false, mode: "index" },
        plugins: {
            legend: { display: false },
            tooltip: {
                filter: (item) => item.datasetIndex === 0,
                callbacks: {
                    title: (items) => items.length ? formatTimestamp(data.series.timestamps[items[0].dataIndex]) : "",
                    label: (item) => `${formatNumber(item.raw, selectedMetric)} ${metricConfig[selectedMetric].unit}`,
                },
            },
        },
        scales: {
            x: { grid: { color: "#edf1f5" }, ticks: { color: "#6b7688", maxRotation: 0, autoSkip: false, callback: chartTick } },
            y: {
                grid: { color: "#edf1f5" },
                ticks: { color: "#6b7688", callback: (value) => formatNumber(value, selectedMetric) },
                title: { display: true, color: "#6b7688", text: metricConfig[selectedMetric].unit, font: { family: "Poppins", size: 11 } },
            },
        },
    });

    const chartData = () => {
        const labels = data.series?.timestamps || [];
        const values = data.series?.metrics?.[selectedMetric] || [];
        const datasets = [{
            label: metricConfig[selectedMetric].label,
            data: values,
            borderColor: "#0b78d0",
            backgroundColor: "#0b78d0",
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 3,
            pointHoverBackgroundColor: "#0b78d0",
            tension: 0.22,
            spanGaps: true,
        }];
        const threshold = metricConfig[selectedMetric].threshold;
        if (threshold !== null && threshold !== undefined) {
            datasets.push({
                label: "Configured threshold",
                data: labels.map(() => threshold),
                borderColor: "#d99b24",
                borderWidth: 1,
                borderDash: [6, 5],
                pointRadius: 0,
                pointHitRadius: 0,
                tension: 0,
            });
        }
        return { labels, datasets };
    };

    const renderChart = () => {
        const labels = data.series?.timestamps || [];
        const hasData = labels.length > 0 && (data.series?.metrics?.[selectedMetric] || []).some((value) => value !== null && value !== undefined);
        const empty = byId("chart-empty-state");
        const fallback = byId("chart-fallback");
        chartCanvas.hidden = !hasData;
        if (empty) empty.hidden = hasData;
        if (fallback) fallback.hidden = Boolean(window.Chart);
        if (!window.Chart || !hasData) return;
        if (!chart) {
            chart = new window.Chart(chartCanvas.getContext("2d"), { type: "line", data: chartData(), options: chartOptions() });
        } else {
            chart.data = chartData();
            chart.options = chartOptions();
            chart.update("none");
        }
        const values = (data.series?.metrics?.[selectedMetric] || []).filter((value) => value !== null && value !== undefined);
        const latest = data.latest?.metrics?.[selectedMetric]?.value;
        chartCanvas.setAttribute("aria-label", `${metricConfig[selectedMetric].label} readings for ${rangeLabels[data.range]}. Latest value ${formatNumber(latest, selectedMetric)} ${metricConfig[selectedMetric].unit}. Maximum ${formatNumber(values.length ? Math.max(...values) : null, selectedMetric)} ${metricConfig[selectedMetric].unit}.`);
    };

    const renderTrend = () => {
        const config = metricConfig[selectedMetric];
        setText("#trend-metric-label", config.short_label);
        setText("#trend-range-label", rangeLabels[data.range] || rangeLabels["24h"]);
        setText("#trend-current", `${formatNumber(data.latest?.metrics?.[selectedMetric]?.value, selectedMetric)} ${config.unit}`);
        document.querySelectorAll(".metric-card").forEach((card) => {
            const selected = card.dataset.metric === selectedMetric;
            card.classList.toggle("is-selected", selected);
            card.setAttribute("aria-pressed", String(selected));
        });
        renderSummary();
        renderChart();
    };

    const render = () => {
        renderFreshness();
        renderLatest();
        renderAlerts();
        renderTrend();
    };

    const rangeSelect = byId("dashboard-range-select");
    const rangeTrigger = byId("dashboard-range-trigger");
    const rangeOptions = [...document.querySelectorAll("#dashboard-range-menu .custom-select-option")];
    const closeRangeMenu = (restoreFocus = false) => {
        if (!rangeSelect || !rangeTrigger) return;
        rangeSelect.classList.remove("is-open");
        rangeTrigger.setAttribute("aria-expanded", "false");
        if (restoreFocus) rangeTrigger.focus();
    };
    const openRangeMenu = (focusSelected = false) => {
        if (!rangeSelect || !rangeTrigger) return;
        rangeSelect.classList.add("is-open");
        rangeTrigger.setAttribute("aria-expanded", "true");
        if (focusSelected) (rangeOptions.find((option) => option.getAttribute("aria-selected") === "true") || rangeOptions[0])?.focus();
    };
    const refresh = async (force = false) => {
        if (document.hidden && !force) return;
        if (requestController) return;
        const generation = ++requestGeneration;
        requestController = new AbortController();
        root.setAttribute("aria-busy", "true");
        try {
            const url = new URL(root.dataset.liveUrl, window.location.origin);
            url.searchParams.set("range", data.range);
            const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" }, signal: requestController.signal });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                window.location.href = response.url;
                return;
            }
            if (response.status === 401 || response.status === 403) {
                window.location.href = "/login";
                return;
            }
            if (!response.ok) throw new Error(`Dashboard refresh failed: ${response.status}`);
            const nextData = await response.json();
            if (generation !== requestGeneration) return;
            data = nextData;
            lastSuccessAt = Date.now();
            render();
        } catch (error) {
            if (error.name !== "AbortError") console.debug("Dashboard refresh skipped", error);
        } finally {
            if (generation === requestGeneration) {
                requestController = null;
                root.setAttribute("aria-busy", "false");
            }
        }
    };

    const chooseRange = (value) => {
        if (!rangeLabels[value] || value === data.range) {
            closeRangeMenu();
            return;
        }
        data.range = value;
        const url = new URL(window.location.href);
        url.searchParams.set("range", value);
        window.history.replaceState({}, "", url);
        rangeOptions.forEach((option) => {
            const selected = option.dataset.value === value;
            option.classList.toggle("is-selected", selected);
            option.setAttribute("aria-selected", String(selected));
        });
        setText("#dashboard-range-value", rangeLabels[value]);
        closeRangeMenu();
        requestGeneration += 1;
        requestController?.abort();
        requestController = null;
        refresh(true);
    };

    rangeTrigger?.addEventListener("click", () => rangeSelect.classList.contains("is-open") ? closeRangeMenu() : openRangeMenu());
    rangeTrigger?.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); openRangeMenu(true); }
        if (event.key === "Escape") closeRangeMenu();
    });
    rangeOptions.forEach((option, index) => {
        option.addEventListener("click", () => chooseRange(option.dataset.value));
        option.addEventListener("keydown", (event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const offset = event.key === "ArrowDown" ? 1 : -1;
                rangeOptions[(index + offset + rangeOptions.length) % rangeOptions.length]?.focus();
            } else if (event.key === "Escape") { event.preventDefault(); closeRangeMenu(true); }
            else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); chooseRange(option.dataset.value); }
        });
    });
    document.addEventListener("click", (event) => { if (rangeSelect && !rangeSelect.contains(event.target)) closeRangeMenu(); });

    document.querySelectorAll(".metric-card").forEach((card) => card.addEventListener("click", () => {
        selectedMetric = card.dataset.metric;
        renderTrend();
    }));

    render();
    window.setInterval(() => {
        if (!document.hidden && Date.now() - lastSuccessAt >= 10000) refresh();
    }, 10000);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && Date.now() - lastSuccessAt >= 10000) refresh(true);
    });
})();
