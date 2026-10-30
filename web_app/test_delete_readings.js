// Run: node web_app/test_delete_readings.js. No database or browser required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "static/delete-readings.js"), "utf8");

function harness(confirmAnswer = false) {
    class Element {
        constructor(properties = {}) {
            Object.assign(this, properties);
            this.listeners = {};
        }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        dispatch(type) { return this.listeners[type]?.(event()); }
        setAttribute(name, value) { this[name] = value; }
        setCustomValidity(message) { this.validationMessage = message; }
        reportValidity() { this.validityReports = (this.validityReports || 0) + 1; }
    }
    const dialog = new Element({ showModalCalls: 0, closeCalls: 0 });
    dialog.showModal = () => dialog.showModalCalls++;
    dialog.close = () => dialog.closeCalls++;
    const openButton = new Element({ disabled: true });
    const cancelButton = new Element();
    const submitButton = new Element({ disabled: true, textContent: "Delete selected readings" });
    const mode = new Element({ value: "from_timestamp" });
    const from = new Element({ value: "", disabled: false, required: true });
    const scope = new Element({ value: "simulated" });
    const scopeSummary = new Element({ textContent: "" });
    const form = new Element({
        elements: { confirmation: { value: "" } },
        submitCalls: 0,
    });
    form.submit = () => form.submitCalls++;
    const elements = {
        "#delete-readings-dialog": dialog,
        "#delete-readings-form": form,
        "#delete-all-readings": openButton,
        "#cancel-delete-readings": cancelButton,
        "#confirm-delete-readings": submitButton,
        "#delete-mode": mode,
        "#delete-from": from,
        "#delete-scope": scope,
        "#delete-scope-summary": scopeSummary,
    };
    const state = { prompts: 0, lastPrompt: "" };
    vm.runInNewContext(source, {
        document: { querySelector(selector) { return elements[selector] || null; } },
        window: { confirm(message) { state.prompts++; state.lastPrompt = message; return confirmAnswer; } },
    });
    return { dialog, openButton, cancelButton, submitButton, mode, from, scope, scopeSummary, form, state };
}

function event() {
    return { prevented: false, preventDefault() { this.prevented = true; } };
}

const cancelled = harness(false);
assert.equal(cancelled.openButton.disabled, false);
assert.equal(cancelled.submitButton.disabled, false);
assert.equal(cancelled.from.disabled, false);
assert.equal(cancelled.from.required, true);
cancelled.openButton.dispatch("click");
cancelled.cancelButton.dispatch("click");
assert.equal(cancelled.dialog.showModalCalls, 1);
assert.equal(cancelled.dialog.closeCalls, 1);
cancelled.from.value = "2026-10-30T08:15";
const cancelledEvent = event();
cancelled.form.listeners.submit(cancelledEvent);
assert.equal(cancelledEvent.prevented, true);
assert.equal(cancelled.form.submitCalls, 0);
assert.equal(cancelled.form.elements.confirmation.value, "");
assert.match(cancelled.state.lastPrompt, /known simulation devices/);
assert.match(cancelled.state.lastPrompt, /2026-10-30 08:15 UTC/);

const invalid = harness(true);
invalid.from.value = "2026-02-30T08:15";
invalid.form.listeners.submit(event());
assert.equal(invalid.state.prompts, 0);
assert.equal(invalid.form.submitCalls, 0);
assert.match(invalid.from.validationMessage, /valid inclusive UTC start time/);
assert.equal(invalid.from.validityReports, 1);

const confirmed = harness(true);
confirmed.from.value = "2026-10-30T08:15:30";
confirmed.form.listeners.submit(event());
assert.equal(confirmed.form.elements.confirmation.value, "delete_from_timestamp");
assert.equal(confirmed.form.submitCalls, 1);
assert.equal(confirmed.submitButton.disabled, true);
assert.equal(confirmed.submitButton["aria-busy"], "true");
assert.match(confirmed.submitButton.textContent, /Deleting/);
confirmed.form.listeners.submit(event());
assert.equal(confirmed.state.prompts, 1);
assert.equal(confirmed.form.submitCalls, 1);

const all = harness(true);
all.mode.value = "all";
all.mode.dispatch("change");
assert.equal(all.from.disabled, true);
assert.equal(all.from.required, false);
assert.equal(all.scope.disabled, true);
assert.equal(all.scope.value, "all");
assert.match(all.scopeSummary.textContent, /Across all timestamps/);
assert.match(all.scopeSummary.textContent, /including the imported dataset/);
all.form.listeners.submit(event());
assert.equal(all.form.elements.confirmation.value, "delete_all");
assert.equal(all.form.submitCalls, 1);
assert.match(all.state.lastPrompt, /ALL timestamps/);
assert.match(all.state.lastPrompt, /EVERY device/);
all.mode.value = "from_timestamp";
all.mode.dispatch("change");
assert.equal(all.scope.disabled, false);
assert.equal(all.scope.value, "simulated");

console.log("PASS: delete controls fail closed, validate UTC timestamps, confirm scope, and submit once.");
