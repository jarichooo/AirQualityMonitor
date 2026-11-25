// Run: node web_app/test_delete_readings.js. No database or browser required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "static/delete-readings.js"), "utf8");

function harness(confirmAnswer = false) {
    class Element {
        constructor(properties = {}) {
            this.attrs = {};
            this.classes = new Set();
            Object.assign(this, properties);
            this.listeners = {};
            this.classList = {
                toggle: (cls, force) => {
                    const shouldHave = force !== undefined ? Boolean(force) : !this.classes.has(cls);
                    if (shouldHave) this.classes.add(cls);
                    else this.classes.delete(cls);
                    return shouldHave;
                },
                contains: (cls) => this.classes.has(cls),
            };
        }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        dispatch(type, customEvent) { return this.listeners[type]?.(customEvent || event()); }
        setAttribute(name, value) { this[name] = value; this.attrs[name] = value; }
        getAttribute(name) { return name in this.attrs ? this.attrs[name] : this[name]; }
        setCustomValidity(message) { this.validationMessage = message; }
        reportValidity() { this.validityReports = (this.validityReports || 0) + 1; }
    }
    const dialog = new Element({ showModalCalls: 0, closeCalls: 0 });
    dialog.showModal = () => dialog.showModalCalls++;
    dialog.close = () => dialog.closeCalls++;
    const openButton = new Element({ disabled: true });
    const cancelButton = new Element();
    const closeButton = new Element();
    const submitButton = new Element({ disabled: true, textContent: "Delete matching readings" });
    const mode = new Element({ value: "from_timestamp" });
    const cutoffPanel = new Element({ hidden: false });
    const fromField = new Element({ hidden: false });
    const from = new Element({ value: "", disabled: false, required: true });
    const scopeField = new Element({ hidden: false });
    const scope = new Element({ value: "simulated" });
    const scopeHint = new Element({ textContent: "" });
    const summaryCard = new Element({});
    const scopeSummary = new Element({ textContent: "" });
    const idBehavior = new Element({ textContent: "" });
    const modeCards = ["from_timestamp", "all_simulated", "incomplete", "all"].map((val) => {
        const card = new Element({});
        card.setAttribute("data-delete-mode", val);
        return card;
    });
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
        "#close-delete-readings": closeButton,
        "#confirm-delete-readings": submitButton,
        "#delete-mode": mode,
        "#delete-cutoff-panel": cutoffPanel,
        "#delete-from-field": fromField,
        "#delete-from": from,
        "#delete-scope-field": scopeField,
        "#delete-scope": scope,
        "#delete-scope-hint": scopeHint,
        "#delete-summary-card": summaryCard,
        "#delete-scope-summary": scopeSummary,
        "#delete-id-behavior": idBehavior,
    };
    const state = { prompts: 0, lastPrompt: "" };
    vm.runInNewContext(source, {
        document: {
            querySelector(selector) { return elements[selector] || null; },
            querySelectorAll(selector) { return selector === "[data-delete-mode]" ? modeCards : []; },
        },
        window: { confirm(message) { state.prompts++; state.lastPrompt = message; return confirmAnswer; } },
    });
    return {
        dialog, openButton, cancelButton, closeButton, submitButton,
        mode, cutoffPanel, fromField, from, scopeField, scope, scopeHint,
        summaryCard, scopeSummary, idBehavior, modeCards, form, state,
    };
}

function event() {
    return { prevented: false, preventDefault() { this.prevented = true; } };
}

const cancelled = harness(false);
assert.equal(cancelled.openButton.disabled, false);
assert.equal(cancelled.submitButton.disabled, false);
assert.equal(cancelled.from.disabled, false);
assert.equal(cancelled.from.required, true);
assert.equal(cancelled.cutoffPanel.hidden, false);
assert.equal(cancelled.fromField.hidden, false);
assert.equal(cancelled.scopeField.hidden, false);
assert.equal(cancelled.summaryCard["data-impact"], "scoped");
assert.match(cancelled.scopeSummary.textContent, /PHT \(UTC\+8\)/);
cancelled.openButton.dispatch("click");
cancelled.cancelButton.dispatch("click");
cancelled.closeButton.dispatch("click");
cancelled.dialog.dispatch("click", { target: cancelled.dialog });
assert.equal(cancelled.dialog.showModalCalls, 1);
assert.equal(cancelled.dialog.closeCalls, 3);
cancelled.from.value = "2026-10-30T08:15";
cancelled.from.dispatch("input");
assert.match(cancelled.scopeSummary.textContent, /Oct 30, 2026, 08:15:00 AM PHT \(UTC\+8\)/);
const cancelledEvent = event();
cancelled.form.listeners.submit(cancelledEvent);
assert.equal(cancelledEvent.prevented, true);
assert.equal(cancelled.form.submitCalls, 0);
assert.equal(cancelled.form.elements.confirmation.value, "");
assert.match(cancelled.state.lastPrompt, /known simulation devices/);
assert.match(cancelled.state.lastPrompt, /Oct 30, 2026, 08:15:00 AM PHT \(UTC\+8\)/);

const invalid = harness(true);
invalid.from.value = "2026-02-30T08:15";
invalid.form.listeners.submit(event());
assert.equal(invalid.state.prompts, 0);
assert.equal(invalid.form.submitCalls, 0);
assert.match(invalid.from.validationMessage, /valid inclusive PHT \(UTC\+8\) start time/);
assert.equal(invalid.from.validityReports, 1);

const confirmed = harness(true);
confirmed.from.value = "2026-10-30T20:15:30";
confirmed.form.listeners.submit(event());
assert.equal(confirmed.form.elements.confirmation.value, "delete_from_timestamp");
assert.equal(confirmed.form.submitCalls, 1);
assert.equal(confirmed.submitButton.disabled, true);
assert.equal(confirmed.submitButton["aria-busy"], "true");
assert.match(confirmed.submitButton.textContent, /Deleting/);
assert.match(confirmed.state.lastPrompt, /Oct 30, 2026, 08:15:30 PM PHT \(UTC\+8\)/);
confirmed.form.listeners.submit(event());
confirmed.closeButton.dispatch("click");
assert.equal(confirmed.state.prompts, 1);
assert.equal(confirmed.form.submitCalls, 1);
assert.equal(confirmed.dialog.closeCalls, 0);

const simCardHarness = harness(true);
simCardHarness.modeCards[1].dispatch("click"); // all_simulated
assert.equal(simCardHarness.mode.value, "all_simulated");
assert.equal(simCardHarness.cutoffPanel.hidden, true);
assert.equal(simCardHarness.fromField.hidden, true);
assert.equal(simCardHarness.scopeField.hidden, true);
assert.equal(simCardHarness.from.disabled, true);
assert.equal(simCardHarness.scope.disabled, true);
assert.equal(simCardHarness.scope.value, "simulated");
assert.equal(simCardHarness.modeCards[1].getAttribute("aria-pressed"), "true");
assert.equal(simCardHarness.modeCards[0].getAttribute("aria-pressed"), "false");
assert.equal(simCardHarness.submitButton.textContent, "Delete simulation readings");

const all = harness(true);
all.mode.value = "all";
all.mode.dispatch("change");
assert.equal(all.from.disabled, true);
assert.equal(all.from.required, false);
assert.equal(all.cutoffPanel.hidden, true);
assert.equal(all.scope.disabled, true);
assert.equal(all.scope.value, "all");
assert.equal(all.summaryCard["data-impact"], "danger");
assert.match(all.idBehavior.textContent, /next reading ID is 1/);
assert.match(all.scopeSummary.textContent, /Across all timestamps/);
assert.match(all.scopeSummary.textContent, /including the imported dataset/);
all.form.listeners.submit(event());
assert.equal(all.form.elements.confirmation.value, "delete_all");
assert.equal(all.form.submitCalls, 1);
assert.match(all.state.lastPrompt, /ALL timestamps/);
assert.match(all.state.lastPrompt, /EVERY device/);
all.mode.value = "from_timestamp";
all.mode.dispatch("change");
assert.equal(all.cutoffPanel.hidden, false);
assert.equal(all.fromField.hidden, false);
assert.equal(all.scopeField.hidden, false);
assert.equal(all.scope.disabled, false);
assert.equal(all.scope.value, "simulated");

console.log("PASS: delete controls fail closed, validate PHT timestamps, toggle cutoff visibility, confirm scope, and submit once.");
