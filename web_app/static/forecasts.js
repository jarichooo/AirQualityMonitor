(() => {
    const panel = document.getElementById("forecast-panel");
    const results = document.getElementById("forecast-results");
    const predictionControls = document.getElementById("prediction-controls");
    const predictButton = document.getElementById("predict-now");
    const predictionStatus = document.getElementById("prediction-status");
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

    const renderForecast = (forecast) => {
        const article = document.createElement("article");
        article.className = `forecast-card is-${forecast.status === "available" ? "available" : "unavailable"}`;
        if (forecast.status === "available" && forecast.predictions?.length) {
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
        }
        const guidanceFallback = forecast.explanation_status === "pending"
            ? "This panel updates automatically."
            : forecast.explanation_status === "error" && forecast.status === "available"
                ? forecast.reason === "QWEN_TIMEOUT"
                    ? "It took too long to respond. Check the Ollama service and forecast worker logs."
                    : "Could not generate output text. Check the Ollama service and forecast worker logs."
                : "No saved guidance yet.";
        if (forecast.explanation) {
            addText(article, "p", forecast.explanation);
        } else if (forecast.last_guidance) {
            const update = forecast.status !== "available"
                ? "Fresh readings are needed."
                : forecast.explanation_status === "pending"
                    ? "Updating guidance for the latest readings…"
                    : "Guidance update failed; showing the last completed guidance.";
            addText(article, "p", update);
            addText(article, "p", forecast.last_guidance.text);
        } else {
            addText(article, "p", guidanceFallback);
        }
        return article;
    };

    const render = (payload) => {
        results.replaceChildren();
        if (!payload.configured) {
            addText(results, "p", payload.message || "Forecasting is not configured yet.");
            return;
        }
        const forecasts = (payload.forecasts || []).filter((forecast) =>
            (forecast.status === "available" && forecast.predictions?.length) || forecast.explanation || forecast.last_guidance?.text
        );
        if (!forecasts.length) {
            addText(results, "p", "No predictions or guidance available yet.");
            return;
        }
        forecasts
            .sort((left, right) => Number(right.status === "available") - Number(left.status === "available"))
            .forEach((forecast) => results.append(renderForecast(forecast)));
    };

    let loading = false;
    let submitting = false;
    let predictionConfigured = false;
    let renderedFingerprint = null;
    let refreshTimer;
    const refreshNotice = addText(panel, "p", "");
    refreshNotice.setAttribute("role", "status");
    const renderPredictionState = (payload) => {
        if (!predictionControls || !predictButton || !predictionStatus) return false;
        if (!payload?.configured) {
            predictionConfigured = false;
            predictionControls.hidden = true;
            predictButton.disabled = true;
            predictionStatus.textContent = "";
            return false;
        }
        predictionConfigured = true;
        predictionControls.hidden = false;
        const busy = ["queued", "running"].includes(payload.status);
        const cooldown = Math.max(0, Number(payload.cooldown_seconds) || 0);
        predictButton.textContent = busy || submitting ? "Predicting…" : "Predict now";
        predictButton.disabled = submitting || busy || cooldown > 0 || !payload.can_predict || !panel.dataset.predictionToken;
        predictionStatus.textContent = busy
            ? payload.message || "Prediction is being generated."
            : cooldown > 0 && payload.status !== "error"
                ? `Prediction available in ${cooldown} second${cooldown === 1 ? "" : "s"}.`
                : payload.message || (payload.can_predict ? "Ready to predict the latest readings." : "");
        return true;
    };
    const scheduleRefresh = (delay) => {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(refresh, delay);
    };
    const refresh = async () => {
        if (loading || document.hidden) return;
        window.clearTimeout(refreshTimer);
        loading = true;
        let refreshDelay = 60000;
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
        if (panel.dataset.predictionUrl) {
            try {
                const predictionResponse = await fetch(panel.dataset.predictionUrl, {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                    cache: "no-store",
                    signal: controller.signal,
                });
                if (predictionResponse.redirected && new URL(predictionResponse.url).pathname === "/login") {
                    window.location.assign(predictionResponse.url);
                    window.clearTimeout(timeout);
                    loading = false;
                    panel.setAttribute("aria-busy", "false");
                    return;
                }
                if (!predictionResponse.ok) throw new Error(`Prediction status failed: ${predictionResponse.status}`);
                if (renderPredictionState(await predictionResponse.json())) refreshDelay = 3000;
            } catch (error) {
                if (predictionConfigured && predictButton && predictionStatus) {
                    predictButton.disabled = true;
                    predictionStatus.textContent = "Could not check prediction availability.";
                }
                refreshDelay = Math.min(refreshDelay, 15000);
            }
        }
        window.clearTimeout(timeout);
        loading = false;
        panel.setAttribute("aria-busy", "false");
        scheduleRefresh(refreshDelay);
    };

    predictButton?.addEventListener("click", async () => {
        if (submitting || predictButton.disabled) return;
        submitting = true;
        window.clearTimeout(refreshTimer);
        predictButton.disabled = true;
        predictButton.textContent = "Predicting…";
        predictionStatus.textContent = "Starting prediction…";
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(panel.dataset.predictionUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { Accept: "application/json", "Content-Type": "application/json" },
                body: JSON.stringify({ csrf_token: panel.dataset.predictionToken }),
                cache: "no-store",
                signal: controller.signal,
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                window.location.assign(response.url);
                return;
            }
            const payload = await response.json();
            submitting = false;
            renderPredictionState(payload);
        } catch (error) {
            predictionStatus.textContent = "Could not start the prediction. Try again.";
        } finally {
            window.clearTimeout(timeout);
            submitting = false;
            scheduleRefresh(3000);
        }
    });

    refresh();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();
