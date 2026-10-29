(() => {
    const panel = document.getElementById("forecast-panel");
    const results = document.getElementById("forecast-results");
    if (!panel || !results) return;

    const addText = (parent, tag, text) => {
        const element = document.createElement(tag);
        element.textContent = text;
        parent.append(element);
        return element;
    };
    const formatTime = (value) => {
        const date = new Date(value);
        return value && Number.isFinite(date.valueOf()) ? `${date.toISOString().slice(0, 19).replace("T", " ")} UTC` : "Unavailable";
    };
    const severityLabel = (value) => ({ normal: "Normal", warning: "Warning", critical: "Critical", unavailable: "Classification unavailable" }[value] || "Unavailable");

    const renderForecast = (forecast) => {
        const article = document.createElement("article");
        addText(article, "h3", `Device ${forecast.device_id}`);
        if (forecast.data_class === "SIMULATED") {
            addText(article, "p", `Simulated readings${forecast.scenario ? ` · ${forecast.scenario} input conditions` : ""}. Forecasts are model estimates.`);
        }
        addText(article, "p", `Input through ${formatTime(forecast.input_cutoff_at)} · Forecast target ${formatTime(forecast.target_at)}`);
        if (forecast.status !== "available") {
            addText(article, "p", forecast.reason || "The forecast is unavailable.");
        } else {
            const heading = addText(article, "h4", "Forecast values (predicted, not measured)");
            const list = document.createElement("dl");
            list.setAttribute("aria-labelledby", heading.id = `forecast-values-${forecast.forecast_id || forecast.device_id}`);
            forecast.predictions.forEach((prediction) => {
                addText(list, "dt", prediction.label);
                addText(list, "dd", `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(prediction.value)} ${prediction.unit} · ${severityLabel(prediction.severity)}`);
            });
            article.append(list);
        }
        addText(article, "h4", "Reasoning AI guidance");
        const guidanceFallback = forecast.explanation_status === "pending"
            ? "Guidance is being prepared."
            : forecast.explanation_status === "error" && forecast.status === "available"
                ? "The numeric forecast is available, but guidance could not be generated."
                : "Guidance is unavailable for this forecast.";
        addText(article, "p", forecast.explanation || guidanceFallback);
        return article;
    };

    const render = (payload) => {
        results.replaceChildren();
        if (!payload.configured) {
            addText(results, "p", payload.message || "Forecasting is not configured yet.");
            return;
        }
        if (!payload.forecasts?.length) {
            addText(results, "p", payload.message || "No saved forecasts are available yet.");
            return;
        }
        payload.forecasts.forEach((forecast) => results.append(renderForecast(forecast)));
    };

    let loading = false;
    let renderedFingerprint = null;
    const refresh = async () => {
        if (loading || document.hidden) return;
        loading = true;
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
                return;
            }
            if (!response.ok) throw new Error(`Forecast refresh failed: ${response.status}`);
            const payload = await response.json();
            const fingerprint = JSON.stringify(payload);
            if (fingerprint !== renderedFingerprint) {
                render(payload);
                renderedFingerprint = fingerprint;
            }
        } catch (error) {
            if (renderedFingerprint !== "request-error") {
                results.replaceChildren();
                addText(results, "p", "Forecasts are temporarily unavailable. Measured readings are unaffected.");
                renderedFingerprint = "request-error";
            }
        } finally {
            window.clearTimeout(timeout);
            loading = false;
            panel.setAttribute("aria-busy", "false");
        }
    };

    refresh();
    window.setInterval(refresh, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();
