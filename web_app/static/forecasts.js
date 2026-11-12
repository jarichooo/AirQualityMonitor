(() => {
    const panel = document.getElementById("forecast-panel");
    const results = document.getElementById("forecast-results");
    const guidanceBanner = document.getElementById("dashboard-notification-slot");
    const guidanceResults = document.getElementById("forecast-guidance-results") || guidanceBanner;
    if (!panel || !results) return;

    const addText = (parent, tag, text, className = "") => {
        const element = document.createElement(tag);
        element.textContent = text;
        if (className) element.className = className;
        parent.append(element);
        return element;
    };
    const severityLabel = (value) => ({ normal: "Normal", warning: "Warning", critical: "Critical", unavailable: "Classification unavailable" }[value] || "Unavailable");
    const severityClass = (value) => ["normal", "warning", "critical"].includes(value) ? value : "unavailable";
    const severityRank = { unavailable: 0, normal: 1, warning: 2, critical: 3 };

    const highestSeverity = (forecasts) => {
        let best = null;
        forecasts.forEach((forecast) => {
            if (forecast.status !== "available" || !Array.isArray(forecast.predictions)) return;
            forecast.predictions.forEach((prediction) => {
                const candidate = severityClass(prediction.severity);
                if (candidate !== "unavailable" && (!best || severityRank[candidate] > severityRank[best])) {
                    best = candidate;
                }
            });
        });
        return best;
    };

    const CHANNEL_THRESHOLDS = {
        mq137_ppm: { normalMax: 1000, warningMax: 5000, unit: "ppm", category: "gas" },
        mq4_ppm: { normalMax: 10, warningMax: 20, unit: "ppm", category: "gas" },
        co2_ppm: { normalMax: 2500, warningMax: 3000, unit: "ppm", category: "gas" },
        temperature_c: { normalMin: 21, normalMax: 32, warningMin: 18, warningMax: 35, unit: "°C", category: "thermal" },
        humidity_perc: { normalMin: 50, normalMax: 70, warningMin: 40, warningMax: 80, unit: "%", category: "moisture" },
        pm1_ugm3: { normalMax: 10, warningMax: 35, unit: "µg/m³", category: "particulate" },
        pm25_ugm3: { normalMax: 35, warningMax: 150, unit: "µg/m³", category: "particulate" },
        pm10_ugm3: { normalMax: 150, warningMax: 1000, unit: "µg/m³", category: "particulate" },
    };
    const CATEGORY_CHECKS = {
        gas: "air exchange, ventilation fans, and litter or gas buildup",
        thermal: "temperature conditions, airflow distribution, and heating or cooling equipment",
        moisture: "airflow, moisture or condensation buildup, and ventilation equipment",
        particulate: "dust sources, airflow, and filtration or ventilation equipment",
    };

    const formatNumber = (value) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);

    const resolveChannelKey = (prediction) => {
        const key = String(prediction?.key || "").toLowerCase();
        if (CHANNEL_THRESHOLDS[key]) return key;
        const label = String(prediction?.label || "").toLowerCase();
        if (label.includes("ammonia")) return "mq137_ppm";
        if (label.includes("methane")) return "mq4_ppm";
        if (label.includes("temp")) return "temperature_c";
        if (label.includes("humidity")) return "humidity_perc";
        if (label.includes("co2")) return "co2_ppm";
        if (label.includes("pm1.0") || label === "pm1") return "pm1_ugm3";
        if (label.includes("pm2.5")) return "pm25_ugm3";
        if (label.includes("pm10")) return "pm10_ugm3";
        return null;
    };

    const formatList = (items) => {
        if (items.length <= 1) return items[0] || "";
        if (items.length === 2) return `${items[0]} and ${items[1]}`;
        return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
    };

    const formatTargetTime = (isoString) => {
        if (!isoString) return null;
        const date = new Date(isoString);
        if (Number.isNaN(date.getTime())) return null;
        return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(date);
    };

    const describeThreshold = (prediction) => {
        const rule = CHANNEL_THRESHOLDS[resolveChannelKey(prediction)];
        if (!rule) return "";
        const unit = rule.unit === "%" ? "%" : ` ${rule.unit}`;
        const sev = severityClass(prediction.severity);
        if (rule.normalMin != null) {
            const normalBand = `${formatNumber(rule.normalMin)}–${formatNumber(rule.normalMax)}${unit}`;
            const hasValue = typeof prediction.value === "number" && Number.isFinite(prediction.value);
            if (sev === "critical") {
                if (hasValue && prediction.value < rule.warningMin) {
                    return `below the ${formatNumber(rule.warningMin)}${unit} warning lower limit; normal range is ${normalBand}`;
                }
                if (hasValue && prediction.value > rule.warningMax) {
                    return `above the ${formatNumber(rule.warningMax)}${unit} warning upper limit; normal range is ${normalBand}`;
                }
                return `outside the ${normalBand} normal range and ${formatNumber(rule.warningMin)}–${formatNumber(rule.warningMax)}${unit} warning bounds`;
            }
            if (sev === "warning") {
                if (hasValue && prediction.value < rule.normalMin) {
                    return `below the ${formatNumber(rule.normalMin)}${unit} normal lower limit and within the ${formatNumber(rule.warningMin)}–${formatNumber(rule.normalMin)}${unit} warning band`;
                }
                if (hasValue && prediction.value > rule.normalMax) {
                    return `above the ${formatNumber(rule.normalMax)}${unit} normal upper limit and within the ${formatNumber(rule.normalMax)}–${formatNumber(rule.warningMax)}${unit} warning band`;
                }
                return `outside the ${normalBand} normal range`;
            }
            return "";
        }
        const normalBand = `0–${formatNumber(rule.normalMax)}${unit}`;
        if (sev === "critical") {
            return `above the ${formatNumber(rule.warningMax)}${unit} warning upper limit; normal range is ${normalBand}`;
        }
        if (sev === "warning") {
            return `above the ${formatNumber(rule.normalMax)}${unit} normal limit and within the ${formatNumber(rule.warningMax)}${unit} warning ceiling`;
        }
        return "";
    };

    const renderForecastValues = (forecast) => {
        const article = document.createElement("article");
        article.className = "forecast-card is-available";
        const heading = addText(article, "h3", "Predicted values in 2 hours");
        const list = document.createElement("dl");
        list.setAttribute("aria-labelledby", heading.id = `forecast-values-${forecast.forecast_id || forecast.device_id}`);
        forecast.predictions.forEach((prediction) => {
            addText(list, "dt", prediction.label);
            const value = addText(
                list,
                "dd",
                `${formatNumber(prediction.value)} ${prediction.unit} · ${severityLabel(prediction.severity)}`,
                `forecast-value is-${severityClass(prediction.severity)}`,
            );
            value.setAttribute("aria-label", `${prediction.label}: ${value.textContent}`);
        });
        article.append(list);
        return article;
    };

    const summarizeForecast = (forecast) => {
        if (forecast.status !== "available" || !Array.isArray(forecast.predictions) || !forecast.predictions.length) {
            return null;
        }
        const valid = forecast.predictions.filter((p) => p && p.label && typeof p.value === "number" && Number.isFinite(p.value));
        const critical = valid.filter((p) => p.severity === "critical");
        const warning = valid.filter((p) => p.severity === "warning");
        const normal = valid.filter((p) => p.severity === "normal");
        const unavailable = valid.filter((p) => p.severity === "unavailable");
        if (!critical.length && !warning.length && !normal.length) {
            return null;
        }
        const targetTime = formatTargetTime(forecast.target_at);
        const heading = forecast.data_class === "SIMULATED"
            ? (targetTime ? `Simulated forecast for ${targetTime}` : "Simulated forecast")
            : (targetTime ? `Forecast for ${targetTime}` : "Forecast");
        const elevated = [...critical, ...warning];

        let overview;
        if (elevated.length) {
            const highlighted = elevated.slice(0, 4).map((p) => {
                const detail = describeThreshold(p);
                return `${p.label} is forecast to reach ${formatNumber(p.value)} ${p.unit} (${p.severity}${detail ? `, ${detail}` : ""})`;
            });
            const extra = elevated.length > 4 ? `; ${elevated.length - 4} additional channel(s) are also forecast above normal limits` : "";
            overview = `${heading} indicates elevated conditions: ${highlighted.join("; ")}${extra}.`;
            if (normal.length === 1) {
                overview += ` ${normal[0].label} is predicted to remain within normal thresholds.`;
            } else if (normal.length > 1) {
                overview += ` Remaining monitored sensors (${formatList(normal.map((p) => p.label))}) are predicted to remain within normal thresholds.`;
            }
        } else {
            const sample = normal.slice(0, 3).map((p) => `${p.label} (${formatNumber(p.value)} ${p.unit})`);
            if (normal.length === 1) {
                overview = `${heading} indicates ${sample[0]} is predicted to remain within normal project thresholds.`;
            } else if (normal.length <= 3) {
                overview = `${heading} indicates ${formatList(sample)} are predicted to remain within normal project thresholds.`;
            } else {
                overview = `${heading} indicates all ${normal.length} monitored sensors (${formatList(normal.map((p) => p.label))}) are predicted to remain within normal project thresholds, including ${formatList(sample)}.`;
            }
        }
        if (unavailable.length) {
            overview += ` Coverage for ${formatList(unavailable.map((p) => p.label))} is unavailable and remains unverified.`;
        }

        const focusAreas = [...new Set(
            elevated.map((p) => CATEGORY_CHECKS[CHANNEL_THRESHOLDS[resolveChannelKey(p)]?.category]).filter(Boolean)
        )];
        const inspectionTarget = focusAreas.length
            ? formatList(focusAreas)
            : "current sensor conditions and ventilation equipment operation";
        let checks;
        if (critical.length) {
            checks = `Recommended checks: compare this forecast against current live readings and confirm sensor validity, then inspect ${inspectionTarget} before predicted critical conditions develop.`;
        } else if (warning.length) {
            checks = `Recommended checks: compare this forecast against current live readings and confirm sensor validity, and check ${inspectionTarget} to help keep levels from drifting toward critical limits.`;
        } else {
            checks = "No critical or warning levels are predicted for this window; keep monitoring current live readings, sensor validity, and routine ventilation and equipment operation.";
        }
        return [overview, checks];
    };

    const addParagraphs = (parent, text, className = "") => {
        const blocks = String(text).split(/\r?\n+/).map((line) => line.trim()).filter(Boolean);
        (blocks.length ? blocks : [String(text)]).forEach((block) => addText(parent, "p", block, className));
    };

    const renderForecastGuidance = (forecast) => {
        const item = document.createElement("div");
        item.className = "prediction-banner-item";
        if (forecast.explanation) {
            addParagraphs(item, forecast.explanation, "prediction-banner-text");
        } else if (forecast.last_guidance) {
            const update = forecast.status !== "available"
                ? "New readings are needed."
                : forecast.explanation_status === "pending"
                    ? "Updating… Showing the previous output."
                    : forecast.explanation_status === "error"
                        ? "Could not update. Showing the previous output."
                        : "Live forecast updated. Showing the previous output.";
            addText(item, "p", update, "prediction-banner-status");
            addParagraphs(item, forecast.last_guidance.text, "prediction-banner-text");
        } else if (forecast.explanation_status === "pending") {
            addText(item, "p", "Loading forecast…", "prediction-banner-text is-muted");
        } else if (forecast.explanation_status === "error" && forecast.status === "available") {
            addText(item, "p", "Could not load the forecast summary. Try again after a new reading arrives.", "prediction-banner-text is-muted");
        } else {
            const paragraphs = summarizeForecast(forecast);
            if (paragraphs) {
                paragraphs.forEach((paragraph) => addText(item, "p", paragraph, "prediction-banner-text"));
            } else {
                addText(item, "p", "No forecast yet.", "prediction-banner-text is-muted");
            }
        }
        return item;
    };

    const render = (payload) => {
        results.replaceChildren();
        guidanceResults?.replaceChildren();
        if (!payload.configured) {
            if (guidanceBanner) guidanceBanner.className = "dashboard-notification-slot";
            const message = payload.message || "Forecasting is not configured yet.";
            addText(results, "p", message);
            if (guidanceResults) addText(guidanceResults, "p", message, "prediction-banner-text is-muted");
            return;
        }
        const forecasts = (payload.forecasts || [])
            .filter((forecast) =>
                (forecast.status === "available" && forecast.predictions?.length) || forecast.explanation || forecast.last_guidance?.text
            )
            .sort((left, right) => Number(right.status === "available") - Number(left.status === "available"));
        if (!forecasts.length) {
            if (guidanceBanner) guidanceBanner.className = "dashboard-notification-slot";
            addText(results, "p", "No results yet.");
            if (guidanceResults) addText(guidanceResults, "p", "No forecast yet.", "prediction-banner-text is-muted");
            return;
        }
        const numericForecasts = forecasts.filter((forecast) => forecast.status === "available" && forecast.predictions?.length);
        if (numericForecasts.length) {
            numericForecasts.forEach((forecast) => results.append(renderForecastValues(forecast)));
        } else {
            addText(results, "p", "No results yet.");
        }
        if (guidanceBanner) {
            const level = highestSeverity(forecasts);
            guidanceBanner.className = level ? `dashboard-notification-slot is-${level}` : "dashboard-notification-slot";
        }
        if (guidanceResults) {
            forecasts.forEach((forecast) => guidanceResults.append(renderForecastGuidance(forecast)));
        }
    };

    let loading = false;
    let pendingRefresh = false;
    let renderedFingerprint = null;
    let refreshTimer;
    const refreshNotice = addText(panel, "p", "");
    refreshNotice.setAttribute("role", "status");
    const scheduleRefresh = (delay) => {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(refresh, delay);
    };
    const refresh = async () => {
        if (document.hidden) return;
        if (loading) {
            pendingRefresh = true;
            return;
        }
        window.clearTimeout(refreshTimer);
        loading = true;
        pendingRefresh = false;
        let refreshDelay = 20000;
        panel.setAttribute("aria-busy", "true");
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(panel.dataset.forecastUrl, {
                credentials: "same-origin",
                headers: { Accept: "application/json" },
                cache: "no-store",
                signal: controller.signal,
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                window.location.assign(response.url);
                window.clearTimeout(timeout);
                loading = false;
                panel.setAttribute("aria-busy", "false");
                return;
            }
            if (!response.ok) throw new Error(`Forecast refresh failed: ${response.status}`);
            const payload = await response.json();
            if (payload.forecasts?.some((forecast) => forecast.status === "available" && forecast.explanation_status === "pending")) {
                refreshDelay = 3000;
            }
            refreshNotice.textContent = "";
            const fingerprint = JSON.stringify(payload);
            if (fingerprint !== renderedFingerprint) {
                render(payload);
                renderedFingerprint = fingerprint;
            }
        } catch (error) {
            refreshNotice.textContent = "Could not refresh forecasts. Showing the last loaded results.";
            refreshDelay = 15000;
        }
        window.clearTimeout(timeout);
        loading = false;
        panel.setAttribute("aria-busy", "false");
        if (pendingRefresh && !document.hidden) {
            pendingRefresh = false;
            refresh();
            return;
        }
        scheduleRefresh(refreshDelay);
    };

    refresh();
    document.addEventListener("charts:updated", () => refresh());
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();
