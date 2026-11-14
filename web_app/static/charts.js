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
    const formatHoverValue = (value) => {
        const num = Number(value);
        return Number.isFinite(num) ? String(Number(num.toFixed(2))) : "--";
    };
    const activeHighlights = new Map();
    const hoverStates = new Map();

    const parseViewBox = (raw) => {
        const parts = String(raw || "-18 14 798 252").trim().split(/\s+/).map(Number);
        if (parts.length === 4 && parts.every((n) => Number.isFinite(n)) && parts[2] > 0 && parts[3] > 0) {
            return parts;
        }
        return [-18, 14, 798, 252];
    };

    const normalizeHoverSeries = (rawSeries, fallbackUnit = "") => (Array.isArray(rawSeries) ? rawSeries : []).map((s) => {
        const points = (Array.isArray(s.hover_points) ? s.hover_points : [])
            .map((pt) => {
                if (!Array.isArray(pt) || pt.length < 4) return null;
                const x = Number(pt[0]);
                const y = Number(pt[1]);
                const value = Number(pt[3]);
                if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(value)) return null;
                return {
                    x,
                    y,
                    time: String(pt[2] || ""),
                    value,
                    kind: pt[4] === "forecast" ? "forecast" : "recorded",
                };
            })
            .filter(Boolean)
            .sort((a, b) => a.x - b.x);
        return {
            column: String(s.column || ""),
            label: String(s.label || ""),
            unit: String(s.unit || fallbackUnit || ""),
            color: safeColor(s.color),
            points,
        };
    });

    const findNearestPoint = (points, targetX) => {
        if (!points.length) return null;
        let lo = 0;
        let hi = points.length - 1;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (points[mid].x < targetX) lo = mid + 1;
            else hi = mid;
        }
        let best = points[lo];
        let bestDist = Math.abs(best.x - targetX);
        if (lo > 0) {
            const prev = points[lo - 1];
            const prevDist = Math.abs(prev.x - targetX);
            if (prevDist < bestDist) {
                best = prev;
                bestDist = prevDist;
            }
        }
        return { point: best, dist: bestDist };
    };

    const findPointNearX = (points, snapX, tolerance, preferredKind) => {
        if (!points.length) return null;
        const found = findNearestPoint(points, snapX);
        if (!found || found.dist > tolerance) return null;
        let best = found.point;
        let bestDist = found.dist;
        for (let i = 0; i < points.length; i += 1) {
            const pt = points[i];
            const dist = Math.abs(pt.x - snapX);
            if (dist > tolerance) {
                if (pt.x > snapX) break;
                continue;
            }
            const betterDist = dist + 1e-6 < bestDist;
            const sameDist = Math.abs(dist - bestDist) <= 1e-6;
            const betterKind = sameDist && preferredKind && pt.kind === preferredKind && best.kind !== preferredKind;
            const preferRecorded = sameDist && !preferredKind && pt.kind === "recorded" && best.kind === "forecast";
            if (betterDist || betterKind || preferRecorded) {
                best = pt;
                bestDist = dist;
            }
        }
        return best;
    };

    const resolveHoverMatches = (state, svgX, highlight) => {
        if (!state || !Array.isArray(state.series) || !state.series.length) return null;
        const plotRight = state.plotRight || 760;
        const clampedX = Math.min(plotRight, Math.max(60, svgX));
        const tolerance = Math.max(2.5, (plotRight - 60) * 0.008);

        let anchorSeries = null;
        let anchorMatch = null;
        if (highlight) {
            const targetSeries = state.series.find((s) => s.column === highlight.col);
            if (targetSeries) {
                const filtered = targetSeries.points.filter((pt) => pt.kind === highlight.kind);
                const found = findNearestPoint(filtered, clampedX);
                if (found) {
                    anchorSeries = targetSeries;
                    anchorMatch = found.point;
                }
            }
        }

        if (!anchorMatch) {
            let bestDist = Infinity;
            state.series.forEach((s) => {
                const found = findNearestPoint(s.points, clampedX);
                if (found && found.dist < bestDist) {
                    bestDist = found.dist;
                    anchorSeries = s;
                    anchorMatch = found.point;
                }
            });
        }

        if (!anchorMatch) return null;

        const snapX = anchorMatch.x;
        const snapTime = anchorMatch.time;
        const matches = [];
        state.series.forEach((s) => {
            const preferredKind = highlight && highlight.col === s.column ? highlight.kind : null;
            const chosen = s === anchorSeries
                ? anchorMatch
                : findPointNearX(s.points, snapX, tolerance, preferredKind);
            if (chosen) {
                const isFocused = Boolean(highlight)
                    && s.column === highlight.col
                    && chosen.kind === highlight.kind;
                const isDimmed = Boolean(highlight) && !isFocused;
                matches.push({ series: s, point: chosen, isFocused, isDimmed });
            }
        });

        return { snapX, snapTime, anchorPoint: anchorMatch, matches };
    };

    const getSvgGeometry = (svg, viewBox) => {
        if (!svg || typeof svg.getBoundingClientRect !== "function") return null;
        const rect = svg.getBoundingClientRect();
        if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null;
        const [vbX, vbY, vbW, vbH] = viewBox;
        const scale = Math.min(rect.width / vbW, rect.height / vbH);
        if (!(scale > 0)) return null;
        const renderedWidth = vbW * scale;
        const renderedHeight = vbH * scale;
        const offsetX = rect.left + (rect.width - renderedWidth) / 2;
        const offsetY = rect.top + (rect.height - renderedHeight) / 2;
        return { rect, vbX, vbY, vbW, vbH, scale, offsetX, offsetY };
    };

    const ensureHoverLayer = (svg) => {
        let layer = svg.querySelector(".trend-hover-layer");
        if (!layer) {
            layer = svgEl("g", { class: "trend-hover-layer", "pointer-events": "none" });
        }
        svg.appendChild(layer);
        return layer;
    };

    const ensureTooltip = (wrap) => {
        let tooltip = wrap.querySelector(".chart-hover-tooltip");
        if (!tooltip) {
            tooltip = document.createElement("div");
            tooltip.className = "chart-hover-tooltip";
            tooltip.setAttribute("role", "status");
            tooltip.setAttribute("aria-live", "off");
            tooltip.hidden = true;
            wrap.appendChild(tooltip);
        }
        return tooltip;
    };

    const clearHoverOverlay = (panel) => {
        if (!panel) return;
        const state = hoverStates.get(panel.id);
        if (state) {
            state.active = false;
            state.lastClientX = null;
            state.lastClientY = null;
        }
        const svg = panel.querySelector("svg.sensor-trend");
        if (svg) {
            svg.classList.remove("is-scrubbing");
            const layer = svg.querySelector(".trend-hover-layer");
            if (layer) layer.replaceChildren();
        }
        const wrap = panel.querySelector(".trend-chart-wrap");
        if (wrap) {
            const tooltip = wrap.querySelector(".chart-hover-tooltip");
            if (tooltip) {
                tooltip.hidden = true;
                tooltip.replaceChildren();
            }
        }
    };

    const renderHoverOverlay = (panel, clientX, clientY) => {
        if (!panel || !Number.isFinite(clientX)) return;
        const state = hoverStates.get(panel.id);
        const svg = panel.querySelector("svg.sensor-trend");
        const wrap = panel.querySelector(".trend-chart-wrap");
        if (!state || !svg || !wrap) return;

        const geom = getSvgGeometry(svg, state.viewBox);
        if (!geom) return;
        const svgX = geom.vbX + (clientX - geom.offsetX) / geom.scale;
        const resolved = resolveHoverMatches(state, svgX, activeHighlights.get(panel.id) || null);
        if (!resolved || !resolved.matches.length) {
            clearHoverOverlay(panel);
            return;
        }

        state.active = true;
        state.lastClientX = clientX;
        state.lastClientY = clientY;
        svg.classList.add("is-scrubbing");

        const layer = ensureHoverLayer(svg);
        const overlayNodes = [
            svgEl("line", {
                class: "trend-crosshair",
                x1: resolved.snapX,
                y1: 24,
                x2: resolved.snapX,
                y2: 232,
            }),
        ];

        const orderedMatches = [
            ...resolved.matches.filter((m) => m.isDimmed),
            ...resolved.matches.filter((m) => !m.isDimmed && !m.isFocused),
            ...resolved.matches.filter((m) => m.isFocused),
        ];
        orderedMatches.forEach(({ series, point, isFocused, isDimmed }) => {
            const dotClass = [
                "trend-hover-dot",
                isFocused ? "is-highlighted" : "",
                isDimmed ? "is-dimmed" : "",
            ].filter(Boolean).join(" ");
            const dot = svgEl("circle", {
                class: dotClass,
                cx: point.x,
                cy: point.y,
                r: isFocused ? "5" : "4.5",
            });
            dot.style.setProperty("--series-color", series.color);
            dot.style.stroke = series.color;
            overlayNodes.push(dot);
        });
        layer.replaceChildren(...overlayNodes);

        const tooltip = ensureTooltip(wrap);
        const timeEl = document.createElement("div");
        timeEl.className = "chart-hover-time";
        timeEl.textContent = resolved.snapTime || "--";

        const rowsEl = document.createElement("div");
        rowsEl.className = "chart-hover-rows";
        resolved.matches.forEach(({ series, point, isFocused, isDimmed }) => {
            const row = document.createElement("div");
            row.className = [
                "chart-hover-row",
                isFocused ? "is-highlighted" : "",
                isDimmed ? "is-dimmed" : "",
            ].filter(Boolean).join(" ");
            row.style.color = series.color;

            const labelEl = document.createElement("span");
            labelEl.className = "chart-hover-label";
            labelEl.textContent = point.kind === "forecast"
                ? `${series.label} (forecast)`
                : series.label;

            const sepEl = document.createElement("span");
            sepEl.className = "chart-hover-sep";
            sepEl.textContent = " : ";

            const valueEl = document.createElement("strong");
            valueEl.className = "chart-hover-value";
            valueEl.textContent = formatHoverValue(point.value);

            row.append(labelEl, sepEl, valueEl);
            const unitText = series.unit || state.unit || "";
            if (unitText) {
                const unitEl = document.createElement("span");
                unitEl.className = "chart-hover-unit";
                unitEl.textContent = ` ${unitText}`;
                row.append(unitEl);
            }
            rowsEl.append(row);
        });

        tooltip.replaceChildren(timeEl, rowsEl);
        tooltip.hidden = false;

        const wrapRect = typeof wrap.getBoundingClientRect === "function"
            ? wrap.getBoundingClientRect()
            : geom.rect;
        const crosshairPxX = (geom.offsetX + (resolved.snapX - geom.vbX) * geom.scale) - wrapRect.left;
        const anchorPxY = (geom.offsetY + (resolved.anchorPoint.y - geom.vbY) * geom.scale) - wrapRect.top;
        const tipW = tooltip.offsetWidth || 210;
        const tipH = tooltip.offsetHeight || 54;
        const wrapW = wrap.clientWidth || wrapRect.width || 0;
        const svgTop = geom.rect.top - wrapRect.top;
        const svgBottom = geom.rect.bottom - wrapRect.top;

        const gap = 14;
        const midSvgX = (60 + (state.plotRight || 760)) / 2;
        const fitsRight = crosshairPxX + gap + tipW <= wrapW - 8;
        const fitsLeft = crosshairPxX - gap - tipW >= 8;
        let left;
        if (resolved.snapX <= midSvgX) {
            left = fitsRight ? (crosshairPxX + gap) : (crosshairPxX - gap - tipW);
        } else {
            left = fitsLeft ? (crosshairPxX - gap - tipW) : (crosshairPxX + gap);
        }
        left = Math.max(8, Math.min(left, Math.max(8, wrapW - tipW - 8)));

        const minTop = Math.max(4, svgTop + 4);
        const maxTop = Math.max(minTop, svgBottom - tipH - 6);
        let top = anchorPxY - tipH * 0.6;
        top = Math.max(minTop, Math.min(top, maxTop));

        tooltip.style.left = `${Math.round(left)}px`;
        tooltip.style.top = `${Math.round(top)}px`;
    };

    const bindChartHover = (panel) => {
        if (!panel || panel.dataset.hoverBound === "true") return;
        const svg = panel.querySelector("svg.sensor-trend");
        if (!svg) return;
        panel.dataset.hoverBound = "true";

        svg.addEventListener("pointerdown", (event) => {
            if (event.pointerType === "touch" || event.pointerType === "pen") {
                try {
                    if (typeof svg.setPointerCapture === "function") {
                        svg.setPointerCapture(event.pointerId);
                    }
                } catch (_) {
                    // Ignore capture errors on synthetic or unsupported pointers.
                }
            }
            renderHoverOverlay(panel, event.clientX, event.clientY);
        });

        svg.addEventListener("pointermove", (event) => {
            renderHoverOverlay(panel, event.clientX, event.clientY);
        });

        svg.addEventListener("pointerup", (event) => {
            if (event.pointerType === "touch" || event.pointerType === "pen") {
                try {
                    if (typeof svg.releasePointerCapture === "function" && svg.hasPointerCapture?.(event.pointerId)) {
                        svg.releasePointerCapture(event.pointerId);
                    }
                } catch (_) {
                    // Ignore release errors.
                }
            }
        });

        svg.addEventListener("pointerleave", () => {
            clearHoverOverlay(panel);
        });

        svg.addEventListener("pointercancel", () => {
            clearHoverOverlay(panel);
        });
    };

    const initInitialHoverData = () => {
        Array.from(grid.querySelectorAll(".sensor-chart-panel")).forEach((panel) => {
            if (!panel.id) return;
            const scriptEl = panel.querySelector(".chart-hover-data");
            if (scriptEl && scriptEl.textContent) {
                try {
                    const data = JSON.parse(scriptEl.textContent);
                    hoverStates.set(panel.id, {
                        plotRight: Number(data.plot_right) || 760,
                        viewBox: parseViewBox(data.view_box),
                        unit: String(data.unit || ""),
                        series: normalizeHoverSeries(data.series, data.unit),
                        active: false,
                        lastClientX: null,
                        lastClientY: null,
                    });
                } catch (_) {
                    // Ignore malformed initial JSON; live /api/charts will populate.
                }
            }
            bindChartHover(panel);
        });
    };

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
            const hoverLayer = svg.querySelector(".trend-hover-layer");
            if (hoverLayer) svg.appendChild(hoverLayer);
        }

        const hoverState = hoverStates.get(panel.id);
        if (hoverState && hoverState.active && hoverState.lastClientX !== null) {
            renderHoverOverlay(panel, hoverState.lastClientX, hoverState.lastClientY);
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
        const viewBoxStr = chart.view_box || "-18 14 798 252";
        const chartTicks = Array.isArray(chart.ticks) ? chart.ticks : (ticks || []);
        const prevHover = hoverStates.get(panel.id);
        const wasActive = Boolean(prevHover && prevHover.active && prevHover.lastClientX !== null);
        const prevClientX = prevHover ? prevHover.lastClientX : null;
        const prevClientY = prevHover ? prevHover.lastClientY : null;

        hoverStates.set(panel.id, {
            plotRight,
            viewBox: parseViewBox(viewBoxStr),
            unit: String(chart.unit || ""),
            series: normalizeHoverSeries(chart.series_list, chart.unit),
            active: wasActive,
            lastClientX: prevClientX,
            lastClientY: prevClientY,
        });
        bindChartHover(panel);

        const svg = panel.querySelector("svg.sensor-trend");
        if (svg) {
            svg.setAttribute("viewBox", viewBoxStr);
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
        if (wasActive) {
            renderHoverOverlay(panel, prevClientX, prevClientY);
        } else {
            clearHoverOverlay(panel);
        }
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

    initInitialHoverData();
    refresh();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();

