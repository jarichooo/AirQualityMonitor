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
                `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(prediction.value)} ${prediction.unit} · ${severityLabel(prediction.severity)}`,
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
        const critical = forecast.predictions.filter((p) => p?.severity === "critical").map((p) => p.label).filter(Boolean);
        const warning = forecast.predictions.filter((p) => p?.severity === "warning").map((p) => p.label).filter(Boolean);
        const normal = forecast.predictions.filter((p) => p?.severity === "normal");
        const elevated = [];
        if (critical.length) elevated.push(`critical levels for ${critical.join(", ")}`);
        if (warning.length) elevated.push(`warning levels for ${warning.join(", ")}`);
        if (elevated.length) return `2-hour forecast predicts ${elevated.join("; ")}.`;
        return normal.length ? "2-hour forecast predicts normal levels across monitored sensors." : null;
    };

    const renderForecastGuidance = (forecast) => {
        const item = document.createElement("div");
        item.className = "prediction-banner-item";
        if (forecast.explanation) {
            addText(item, "p", forecast.explanation, "prediction-banner-text");
        } else if (forecast.last_guidance) {
            const update = forecast.status !== "available"
                ? "New readings are needed."
                : forecast.explanation_status === "pending"
                    ? "Updating… Showing the previous output."
                    : forecast.explanation_status === "error"
                        ? "Could not update. Showing the previous output."
                        : "Live forecast updated. Showing the previous output.";
            addText(item, "p", update, "prediction-banner-status");
            addText(item, "p", forecast.last_guidance.text, "prediction-banner-text");
        } else if (forecast.explanation_status === "pending") {
            addText(item, "p", "Loading forecast…", "prediction-banner-text is-muted");
        } else if (forecast.explanation_status === "error" && forecast.status === "available") {
            addText(item, "p", "Could not load the forecast summary. Try again after a new reading arrives.", "prediction-banner-text is-muted");
        } else {
            const summary = summarizeForecast(forecast);
            if (summary) {
                addText(item, "p", summary, "prediction-banner-text");
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
