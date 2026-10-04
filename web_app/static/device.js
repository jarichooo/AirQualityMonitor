(() => {
    const root = document.querySelector("[data-device-socket]");
    if (!root) return;

    const form = document.querySelector("#device-connect-form");
    const address = document.querySelector("#device-websocket-url");
    const status = document.querySelector("#device-live-status");
    const dot = document.querySelector("#device-live-dot");
    const time = document.querySelector("#device-live-time");
    const fields = [...document.querySelectorAll("[data-live-sensor]")];
    const storageKey = "henvironment-esp32-websocket";
    let socket;
    let reconnectTimer;
    let reconnectDelay = 1000;
    let manuallyClosed = false;

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
        showStatus(`Connecting to ${endpoint.host}`, "is-delayed");
        const connection = new WebSocket(endpoint.href);
        socket = connection;
        connection.addEventListener("open", () => {
            if (socket !== connection) return;
            reconnectDelay = 1000;
            showStatus("Connected; waiting for a sensor reading", "is-current");
        });
        connection.addEventListener("message", (event) => {
            if (socket !== connection) return;
            try {
                renderReading(JSON.parse(event.data));
            } catch (_) {
                showStatus("Received an invalid device reading", "is-unavailable");
            }
        });
        connection.addEventListener("error", () => {
            if (socket === connection) showStatus("Cannot reach the ESP32", "is-unavailable");
        });
        connection.addEventListener("close", () => {
            if (socket !== connection || manuallyClosed) return;
            showStatus("Disconnected; reconnecting", "is-unavailable");
            reconnectTimer = setTimeout(connect, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 15000);
        });
    };

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        connect();
    });
    window.addEventListener("pagehide", () => {
        manuallyClosed = true;
        clearTimeout(reconnectTimer);
        socket?.close();
    });
    connect();
})();
