(() => {
    const root = document.querySelector("[data-device-socket]");
    if (!root) return;

    const form = document.querySelector("#device-connect-form");
    const address = document.querySelector("#device-websocket-url");
    const status = document.querySelector("#device-live-status");
    const dot = document.querySelector("#device-live-dot");
    const time = document.querySelector("#device-live-time");
    const fields = [...document.querySelectorAll("[data-live-sensor]")];
    const pauseButton = document.querySelector("#device-pause-toggle");
    const shutdownButton = document.querySelector("#server-shutdown-button");
    const collectionState = document.querySelector("#device-collection-state");
    const collectionHelp = document.querySelector("#device-collection-help");
    const storageKey = "henvironment-esp32-websocket";
    const defaultEndpoints = [
        "ws://airqualitymonitor.local:81/",
        "ws://192.168.11.55:81/",
    ];
    const connectTimeoutMs = 4000;
    const fallbackRetryMs = 250;
    let socket;
    let reconnectTimer;
    let connectTimer;
    let reconnectDelay = 1000;
    let manuallyClosed = false;
    let readingsPaused = null;
    let controlTimer;
    let candidateEndpoints = [];
    let endpointIndex = 0;

    const unknownControl = (message) => {
        clearTimeout(controlTimer);
        readingsPaused = null;
        if (pauseButton) {
            pauseButton.disabled = true;
            pauseButton.textContent = "IoT offline";
            pauseButton.removeAttribute("aria-pressed");
            pauseButton.title = message || "";
        }
        if (collectionState) {
            collectionState.textContent = "Status unknown";
            collectionState.dataset.state = "unknown";
        }
        if (collectionHelp) collectionHelp.textContent = message;
    };

    const sendControl = (command) => {
        clearTimeout(controlTimer);
        if (pauseButton) {
            pauseButton.disabled = true;
            pauseButton.textContent = command === "PAUSE" ? "Pausing IoT…" : command === "RESUME" ? "Resuming IoT…" : "Connecting…";
        }
        if (collectionHelp) {
            collectionHelp.textContent = "Waiting for the device to confirm. The displayed status has not changed yet.";
        }
        controlTimer = setTimeout(() => {
            unknownControl("The device did not confirm the change. Click Connect to retry; check the device software if this keeps happening.");
        }, 8000);
        try {
            socket.send(command);
        } catch (_) {
            unknownControl("Could not send the command. Reconnect to the device.");
        }
    };

    try {
        if (address) address.value = localStorage.getItem(storageKey) || address.value;
    } catch (_) {
        // The form still works when browser storage is disabled.
    }

    const showStatus = (message, state) => {
        if (status) status.textContent = message;
        if (dot) {
            dot.className = `freshness-dot ${state}`;
            dot.title = message;
        }
    };

    const displayReading = (value) => {
        if (typeof value !== "number" || !Number.isFinite(value)) return "—";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
    };

    const renderControl = (message) => {
        if (typeof message.paused !== "boolean") throw new Error("Invalid collection status");
        clearTimeout(controlTimer);
        readingsPaused = message.paused;
        if (pauseButton) {
            pauseButton.disabled = false;
            pauseButton.textContent = readingsPaused ? "Resume IoT" : "Pause IoT";
            pauseButton.setAttribute("aria-pressed", String(readingsPaused));
            pauseButton.title = readingsPaused
                ? "IoT device is paused. Click to resume readings."
                : "IoT device is collecting every minute. Click to pause.";
        }
        if (collectionState) {
            collectionState.textContent = readingsPaused ? "Paused" : "Collecting";
            collectionState.dataset.state = readingsPaused ? "paused" : "running";
        }
        if (collectionHelp) {
            collectionHelp.textContent = readingsPaused
                ? "Stored and live readings are paused. Click Resume IoT to continue."
                : "A new reading is sent every minute. Pausing stops uploads from this device.";
        }
        showStatus(readingsPaused ? "Connected · Readings paused" : "Connected · Sending every minute", "is-current");
    };

    const renderReading = (reading) => {
        if (!reading || typeof reading !== "object" || Array.isArray(reading)) {
            throw new Error("Expected a JSON reading object");
        }
        fields.forEach((field) => {
            const key = field.dataset.liveSensor;
            const present = Object.hasOwn(reading, key);
            field.textContent = present ? displayReading(reading[key]) : "—";
            const label = document.querySelector(`[data-live-valid="${key}"]`);
            if (label) label.textContent = present && typeof reading[key] === "number" ? "Live reading" : "Sensor unavailable";
        });
        const date = reading.ts ? new Date(reading.ts) : new Date();
        if (time) {
            time.textContent = Number.isNaN(date.getTime()) ? "Invalid device timestamp" : date.toLocaleString();
        }
        if (readingsPaused === true) {
            unknownControl("The device sent a reading after confirming pause. Reconnect and check the device software.");
        }
        showStatus(`Connected${reading.device_id ? ` · ${reading.device_id}` : ""}`, "is-current");
    };

    const parseEndpoint = (raw) => {
        if (!raw || typeof raw !== "string") return null;
        try {
            const parsed = new URL(raw.trim());
            if (!["ws:", "wss:"].includes(parsed.protocol) || !parsed.hostname) return null;
            return parsed;
        } catch (_) {
            return null;
        }
    };

    const resolveEndpoints = () => {
        if (address) {
            const custom = parseEndpoint(address.value);
            return custom ? [custom] : [];
        }
        const candidates = [];
        const seen = new Set();
        const pushCandidate = (raw) => {
            const parsed = parseEndpoint(raw);
            if (!parsed || seen.has(parsed.href)) return;
            seen.add(parsed.href);
            candidates.push(parsed);
        };
        try {
            pushCandidate(localStorage.getItem(storageKey));
        } catch (_) {
            // Unavailable storage still falls back to default device addresses.
        }
        defaultEndpoints.forEach(pushCandidate);
        return candidates;
    };

    const advanceAndReconnect = (wasOpened) => {
        clearTimeout(connectTimer);
        clearTimeout(reconnectTimer);
        unknownControl("Collection status is unknown until the device reconnects.");
        showStatus("Disconnected; reconnecting", "is-unavailable");
        if (!wasOpened && candidateEndpoints.length > 1) {
            endpointIndex = (endpointIndex + 1) % candidateEndpoints.length;
            if (endpointIndex !== 0) {
                reconnectTimer = setTimeout(() => connect(), fallbackRetryMs);
                return;
            }
        }
        reconnectTimer = setTimeout(() => connect(), reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };

    const connect = ({ resetCandidates = false } = {}) => {
        clearTimeout(reconnectTimer);
        clearTimeout(connectTimer);
        manuallyClosed = false;
        if (resetCandidates || candidateEndpoints.length === 0) {
            candidateEndpoints = resolveEndpoints();
            endpointIndex = 0;
        }
        if (candidateEndpoints.length === 0) {
            showStatus("Enter a valid ws:// or wss:// address", "is-unavailable");
            return;
        }
        const endpoint = candidateEndpoints[endpointIndex % candidateEndpoints.length];

        if (address) {
            try {
                localStorage.setItem(storageKey, endpoint.href);
            } catch (_) {
                // A failed preference save does not prevent a connection.
            }
        }
        socket?.close();
        let opened = false;
        unknownControl(`Connecting to ${endpoint.host}; waiting for its collection status.`);
        showStatus(`Connecting to ${endpoint.host}`, "is-delayed");
        const connection = new WebSocket(endpoint.href);
        socket = connection;
        if (candidateEndpoints.length > 1) {
            connectTimer = setTimeout(() => {
                if (socket !== connection || opened || manuallyClosed) return;
                socket = null;
                try {
                    connection.close();
                } catch (_) {
                    // Ignore errors when aborting a timed-out handshake.
                }
                advanceAndReconnect(false);
            }, connectTimeoutMs);
        }
        connection.addEventListener("open", () => {
            if (socket !== connection) return;
            opened = true;
            clearTimeout(connectTimer);
            reconnectDelay = 1000;
            if (!address) {
                try {
                    localStorage.setItem(storageKey, endpoint.href);
                } catch (_) {
                    // A failed preference save does not prevent a connection.
                }
            }
            sendControl("STATUS");
            showStatus("Connected; waiting for a sensor reading", "is-current");
        });
        connection.addEventListener("message", (event) => {
            if (socket !== connection) return;
            try {
                const message = JSON.parse(event.data);
                if (message.type === "control") renderControl(message);
                else renderReading(message);
            } catch (_) {
                showStatus("Received an invalid device reading", "is-unavailable");
            }
        });
        connection.addEventListener("error", () => {
            if (socket === connection) showStatus("Cannot reach the device", "is-unavailable");
        });
        connection.addEventListener("close", () => {
            if (socket !== connection || manuallyClosed) return;
            advanceAndReconnect(opened);
        });
    };

    form?.addEventListener("submit", (event) => {
        event.preventDefault();
        connect({ resetCandidates: true });
    });
    pauseButton?.addEventListener("click", () => {
        if (!socket || socket.readyState !== WebSocket.OPEN || readingsPaused === null) return;
        sendControl(readingsPaused ? "RESUME" : "PAUSE");
    });
    let shutdownAwaitingHealth = false;
    const resetShutdownButton = () => {
        shutdownAwaitingHealth = false;
        if (!shutdownButton) return;
        shutdownButton.disabled = false;
        shutdownButton.textContent = "Shutdown server";
    };
    document.addEventListener("server:reconnected", resetShutdownButton);
    document.addEventListener("server:health-ok", () => {
        if (shutdownAwaitingHealth) resetShutdownButton();
    });
    shutdownButton?.addEventListener("click", async () => {
        const url = shutdownButton.dataset.shutdownUrl;
        const token = shutdownButton.dataset.shutdownToken;
        if (!url || !window.confirm("Shut down the server now?")) return;
        shutdownAwaitingHealth = false;
        shutdownButton.disabled = true;
        shutdownButton.textContent = "Shutting down…";
        const controller = typeof AbortController === "function" ? new AbortController() : null;
        const timeout = controller ? window.setTimeout(() => controller.abort(), 8000) : null;
        try {
            const response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ csrf_token: token }),
                signal: controller?.signal,
            });
            if (response.redirected && new URL(response.url, window.location.origin).pathname === "/login") {
                resetShutdownButton();
                window.location.href = response.url;
                return;
            }
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload || payload.status !== "shutting_down") {
                const rejection = new Error(payload?.message || "Unable to shut down the server.");
                rejection.isServerRejection = true;
                throw rejection;
            }
            if (typeof document.dispatchEvent === "function" && typeof CustomEvent === "function") {
                document.dispatchEvent(new CustomEvent("server:shutting-down", {
                    detail: { message: payload.message },
                }));
            }
        } catch (error) {
            if (error?.isServerRejection) {
                resetShutdownButton();
                window.alert(error.message);
                return;
            }
            shutdownAwaitingHealth = true;
            if (typeof document.dispatchEvent === "function" && typeof CustomEvent === "function") {
                document.dispatchEvent(new CustomEvent("server:request-failed", {
                    detail: { source: "shutdown" },
                }));
            }
        } finally {
            if (timeout !== null) window.clearTimeout(timeout);
        }
    });
    window.addEventListener("pagehide", () => {
        manuallyClosed = true;
        clearTimeout(reconnectTimer);
        clearTimeout(connectTimer);
        clearTimeout(controlTimer);
        socket?.close();
    });
    connect({ resetCandidates: true });
})();
