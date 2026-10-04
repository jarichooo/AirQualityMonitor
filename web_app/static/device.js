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
    const collectionState = document.querySelector("#device-collection-state");
    const collectionHelp = document.querySelector("#device-collection-help");
    const storageKey = "henvironment-esp32-websocket";
    let socket;
    let reconnectTimer;
    let reconnectDelay = 1000;
    let manuallyClosed = false;
    let readingsPaused = null;
    let controlTimer;

    const unknownControl = (message) => {
        clearTimeout(controlTimer);
        readingsPaused = null;
        pauseButton.disabled = true;
        pauseButton.textContent = "Control unavailable";
        pauseButton.removeAttribute("aria-pressed");
        collectionState.textContent = "Collection status unknown";
        collectionState.dataset.state = "unknown";
        collectionHelp.textContent = message;
    };

    const sendControl = (command) => {
        clearTimeout(controlTimer);
        pauseButton.disabled = true;
        pauseButton.textContent = command === "PAUSE" ? "Pausing…" : command === "RESUME" ? "Resuming…" : "Checking ESP32…";
        collectionHelp.textContent = "Waiting for the ESP32 to confirm. Collection has not changed until confirmed.";
        controlTimer = setTimeout(() => {
            unknownControl("ESP32 did not confirm the command. Click Connect to retry; upload the updated Arduino sketch if control remains unavailable.");
        }, 8000);
        try {
            socket.send(command);
        } catch (_) {
            unknownControl("Could not send the command. Reconnect to the ESP32.");
        }
    };

    try {
        address.value = localStorage.getItem(storageKey) || address.value;
    } catch (_) {
        // The form still works when browser storage is disabled.
    }

    const showStatus = (message, state) => {
        status.textContent = message;
        dot.className = `freshness-dot ${state}`;
    };

    const displayReading = (value) => {
        if (typeof value !== "number" || !Number.isFinite(value)) return "—";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
    };

    const renderControl = (message) => {
        if (typeof message.paused !== "boolean") throw new Error("Invalid collection status");
        clearTimeout(controlTimer);
        readingsPaused = message.paused;
        pauseButton.disabled = false;
        pauseButton.textContent = readingsPaused ? "Resume readings" : "Pause readings";
        pauseButton.setAttribute("aria-pressed", String(readingsPaused));
        collectionState.textContent = readingsPaused ? "Paused — ESP32 confirmed" : "Running — ESP32 confirmed";
        collectionState.dataset.state = readingsPaused ? "paused" : "running";
        collectionHelp.textContent = readingsPaused
            ? "MQTT uploads and live readings are stopped. Click Resume readings to continue."
            : "The ESP32 sends a new reading every minute. Pause stops uploads from this device.";
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
            if (label) label.textContent = present && typeof reading[key] === "number" ? "Live sensor value" : "Sensor unavailable";
        });
        const date = reading.ts ? new Date(reading.ts) : new Date();
        time.textContent = Number.isNaN(date.getTime()) ? "Invalid device timestamp" : date.toLocaleString();
        if (readingsPaused === true) {
            unknownControl("The ESP32 sent a reading after confirming pause. Reconnect and check that the updated sketch is flashed on this device.");
        }
        showStatus(`Connected${reading.device_id ? ` · ${reading.device_id}` : ""}`, "is-current");
    };

    const connect = () => {
        clearTimeout(reconnectTimer);
        manuallyClosed = false;
        let endpoint;
        try {
            endpoint = new URL(address.value.trim());
            if (!["ws:", "wss:"].includes(endpoint.protocol) || !endpoint.hostname) throw new Error();
        } catch (_) {
            showStatus("Enter a valid ws:// or wss:// address", "is-unavailable");
            return;
        }

        try {
            localStorage.setItem(storageKey, endpoint.href);
        } catch (_) {
            // A failed preference save does not prevent a connection.
        }
        socket?.close();
        unknownControl("Connecting to the ESP32; waiting for its collection status.");
        showStatus(`Connecting to ${endpoint.host}`, "is-delayed");
        const connection = new WebSocket(endpoint.href);
        socket = connection;
        connection.addEventListener("open", () => {
            if (socket !== connection) return;
            reconnectDelay = 1000;
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
            if (socket === connection) showStatus("Cannot reach the ESP32", "is-unavailable");
        });
        connection.addEventListener("close", () => {
            if (socket !== connection || manuallyClosed) return;
            unknownControl("ESP32 disconnected. Its collection status cannot be confirmed.");
            showStatus("Disconnected; reconnecting", "is-unavailable");
            reconnectTimer = setTimeout(connect, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 15000);
        });
    };

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        connect();
    });
    pauseButton.addEventListener("click", () => {
        if (!socket || socket.readyState !== WebSocket.OPEN || readingsPaused === null) return;
        sendControl(readingsPaused ? "RESUME" : "PAUSE");
    });
    window.addEventListener("pagehide", () => {
        manuallyClosed = true;
        clearTimeout(reconnectTimer);
        clearTimeout(controlTimer);
        socket?.close();
    });
    connect();
})();
