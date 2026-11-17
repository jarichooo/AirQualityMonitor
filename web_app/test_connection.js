// Run: node web_app/test_connection.js. No database or browser required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSource = fs.readFileSync(path.join(__dirname, "static/app.js"), "utf8");
const deviceSource = fs.readFileSync(path.join(__dirname, "static/device.js"), "utf8");

class Element {
    constructor(id = "", dataset = {}) {
        this.id = id;
        this.hidden = true;
        this.disabled = false;
        this.textContent = "";
        this.dataset = { healthUrl: "/api/health", ...dataset };
        this.listeners = {};
        const classes = new Set();
        this.classList = {
            add: (...items) => items.forEach((item) => classes.add(item)),
            remove: (...items) => items.forEach((item) => classes.delete(item)),
            toggle: (item, force) => {
                const enable = force === undefined ? !classes.has(item) : Boolean(force);
                if (enable) classes.add(item);
                else classes.delete(item);
                return enable;
            },
            contains: (item) => classes.has(item),
        };
    }
    addEventListener(type, handler) {
        (this.listeners[type] ||= []).push(handler);
    }
    removeAttribute() {}
    setAttribute() {}
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

async function run() {
    const notice = new Element("server-connection-notice");
    const kicker = new Element("server-connection-kicker-text");
    const title = new Element("server-connection-title");
    const message = new Element("server-connection-message");
    const retry = new Element("server-connection-retry");
    retry.hidden = false;
    retry.textContent = "Retry now";

    const deviceRoot = new Element("device-root");
    const shutdownButton = new Element("server-shutdown-button", {
        shutdownUrl: "/api/system/shutdown",
        shutdownToken: "csrf-test-token",
    });
    shutdownButton.hidden = false;
    shutdownButton.textContent = "Shutdown server";

    const docListeners = {};
    const winListeners = {};
    const dispatched = [];
    const scheduled = [];
    const alerts = [];
    let nextTimerId = 1;
    let healthOutcomes = [];
    let shutdownOutcomes = [];

    const navigatorObj = { onLine: true };
    const documentObj = {
        hidden: false,
        body: new Element("body"),
        querySelector: (selector) => ({
            "#server-connection-notice": notice,
            "#server-connection-kicker-text": kicker,
            "#server-connection-title": title,
            "#server-connection-message": message,
            "#server-connection-retry": retry,
            "[data-device-socket]": deviceRoot,
            "#server-shutdown-button": shutdownButton,
        }[selector] || null),
        querySelectorAll: () => [],
        addEventListener: (type, handler) => {
            (docListeners[type] ||= []).push(handler);
        },
        dispatchEvent: (event) => {
            dispatched.push(event);
            (docListeners[event.type] || []).forEach((handler) => handler(event));
            return true;
        },
    };
    const windowObj = {
        location: { href: "http://localhost/dashboard", origin: "http://localhost" },
        confirm: () => true,
        alert: (msg) => alerts.push(msg),
        setTimeout: (fn, delay) => {
            const id = nextTimerId++;
            scheduled.push({ id, fn, delay, cleared: false });
            return id;
        },
        clearTimeout: (id) => {
            const entry = scheduled.find((item) => item.id === id);
            if (entry) entry.cleared = true;
        },
        addEventListener: (type, handler) => {
            (winListeners[type] ||= []).push(handler);
        },
        dispatchEvent: (event) => {
            (winListeners[event.type] || []).forEach((handler) => handler(event));
            return true;
        },
    };

    const emitDoc = (type, detail) => documentObj.dispatchEvent({ type, detail });
    const latestActiveTimer = () => [...scheduled].reverse().find((item) => !item.cleared);

    const fetch = async (url) => {
        if (url === "/api/system/shutdown") {
            const next = shutdownOutcomes.shift();
            if (next instanceof Error) throw next;
            return {
                ok: next.status >= 200 && next.status < 300,
                status: next.status,
                redirected: Boolean(next.redirected),
                url: next.url || url,
                json: async () => next.body,
            };
        }
        assert.equal(url, "/api/health");
        const next = healthOutcomes.shift();
        if (next instanceof Error) throw next;
        return {
            ok: next.status >= 200 && next.status < 300,
            status: next.status,
            json: async () => next.body,
        };
    };

    const context = {
        window: windowObj,
        document: documentObj,
        navigator: navigatorObj,
        fetch,
        URL,
        AbortController: class { abort() {} },
        CustomEvent: class {
            constructor(type, init = {}) {
                this.type = type;
                this.detail = init.detail;
            }
        },
        WebSocket: class {
            addEventListener() {}
            close() {}
            send() {}
        },
        localStorage: { getItem: () => null, setItem: () => {} },
    };

    vm.runInNewContext(appSource, context);
    vm.runInNewContext(deviceSource, context);

    // 1. Healthy startup schedules normal 15s poll and keeps notice hidden.
    assert.equal(notice.hidden, true);
    assert.equal(latestActiveTimer().delay, 15000);

    // 2. Single transient chart failure triggers a quick 1.5s confirm without flashing banner; recovery keeps it hidden.
    healthOutcomes.push(new Error("ECONNRESET"), { status: 200, body: { status: "ok", shutting_down: false } });
    emitDoc("server:request-failed", { source: "charts" });
    await settle();
    assert.equal(notice.hidden, true);
    assert.equal(latestActiveTimer().delay, 1500);
    await latestActiveTimer().fn();
    await settle();
    assert.equal(notice.hidden, true);
    assert.equal(dispatched.filter((e) => e.type === "server:reconnected").length, 0);

    // 3. Confirmed server disconnect (2 consecutive failures) displays banner and clears + emits server:reconnected on recovery.
    healthOutcomes.push(new Error("Failed to fetch"), new Error("Failed to fetch"));
    emitDoc("server:request-failed", { source: "charts" });
    await settle();
    assert.equal(notice.hidden, true);
    await latestActiveTimer().fn();
    await settle();
    assert.equal(notice.hidden, false);
    assert.equal(notice.classList.contains("is-shutdown"), false);
    assert.equal(kicker.textContent, "Connection lost");
    assert.equal(title.textContent, "Server disconnected");
    assert.equal(latestActiveTimer().delay, 5000);

    healthOutcomes.push({ status: 200, body: { status: "ok", shutting_down: false } });
    await latestActiveTimer().fn();
    await settle();
    assert.equal(notice.hidden, true);
    assert.equal(dispatched.filter((e) => e.type === "server:reconnected").length, 1);

    // 4. Clicking shutdown disables button, shows amber shutdown banner, survives 503 + unreachable host, and resets on recovery.
    shutdownOutcomes.push({ status: 202, body: { status: "shutting_down", message: "Server is shutting down." } });
    await shutdownButton.listeners.click[0]();
    await settle();
    assert.equal(shutdownButton.disabled, true);
    assert.equal(shutdownButton.textContent, "Shutting down…");
    assert.equal(notice.hidden, false);
    assert.equal(notice.classList.contains("is-shutdown"), true);
    assert.equal(kicker.textContent, "Host shutdown");
    assert.equal(title.textContent, "Server is shutting down");
    assert.equal(message.textContent, "Server is shutting down.");
    assert.equal(latestActiveTimer().delay, 10000);

    healthOutcomes.push({ status: 503, body: { status: "shutting_down", shutting_down: true } });
    await latestActiveTimer().fn();
    await settle();
    assert.equal(notice.hidden, false);
    assert.equal(notice.classList.contains("is-shutdown"), true);
    assert.equal(shutdownButton.disabled, true);

    healthOutcomes.push(new Error("Host powered off"));
    await latestActiveTimer().fn();
    await settle();
    assert.equal(notice.hidden, false);
    assert.equal(notice.classList.contains("is-shutdown"), true);
    assert.equal(kicker.textContent, "Host shutdown");
    assert.equal(latestActiveTimer().delay, 5000);

    healthOutcomes.push({ status: 200, body: { status: "ok", shutting_down: false } });
    retry.listeners.click[0]();
    await settle();
    assert.equal(notice.hidden, true);
    assert.equal(dispatched.filter((e) => e.type === "server:reconnected").length, 2);
    assert.equal(shutdownButton.disabled, false);
    assert.equal(shutdownButton.textContent, "Shutdown server");

    // 5. Redirect to /login during shutdown click resets the button and navigates instead of getting stuck.
    shutdownOutcomes.push({ status: 200, redirected: true, url: "http://localhost/login", body: {} });
    await shutdownButton.listeners.click[0]();
    await settle();
    assert.equal(shutdownButton.disabled, false);
    assert.equal(shutdownButton.textContent, "Shutdown server");
    assert.equal(windowObj.location.href, "http://localhost/login");
    assert.equal(alerts.length, 0);

    // 6. Offline browser event displays network disconnected banner immediately and recovers on online.
    navigatorObj.onLine = false;
    winListeners.offline.forEach((fn) => fn());
    assert.equal(notice.hidden, false);
    assert.equal(kicker.textContent, "Offline");
    assert.equal(title.textContent, "Network disconnected");

    navigatorObj.onLine = true;
    healthOutcomes.push({ status: 200, body: { status: "ok", shutting_down: false } });
    winListeners.online.forEach((fn) => fn());
    await settle();
    assert.equal(notice.hidden, true);
    assert.equal(dispatched.filter((e) => e.type === "server:reconnected").length, 3);

    console.log("test_connection.js passed");
}

run().catch((err) => {
    console.error(err);
    process.exit(1);
});
