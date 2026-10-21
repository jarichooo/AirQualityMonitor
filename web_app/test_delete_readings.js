// Run: node web_app/test_delete_readings.js. No database or browser required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
let submit, answer = false, prompts = 0;
const button = { disabled: true, textContent: "Delete all readings", setAttribute(name, value) { this[name] = value; } };
const form = { elements: { confirmation: { value: "" } }, addEventListener(type, callback) { submit = callback; } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "static/delete-readings.js"), "utf8"), {
    document: { querySelector(selector) { return selector === "#delete-readings-form" ? form : button; } },
    window: { confirm(message) { prompts++; assert.match(message, /ALL sensor readings/); assert.match(message, /ID 1/); return answer; } },
});
const event = () => ({ prevented: false, preventDefault() { this.prevented = true; } });
assert.equal(button.disabled, false);
let cancelled = event();
submit(cancelled);
assert.equal(cancelled.prevented, true);
assert.equal(form.elements.confirmation.value, "");
assert.equal(button.disabled, false);
answer = true;
let confirmed = event();
submit(confirmed);
assert.equal(confirmed.prevented, false);
assert.equal(form.elements.confirmation.value, "delete_all");
assert.equal(button.disabled, true);
assert.equal(button["aria-busy"], "true");
assert.match(button.textContent, /Deleting/);
let duplicate = event();
submit(duplicate);
assert.equal(duplicate.prevented, true);
assert.equal(prompts, 2);
console.log("PASS: cancelling does not submit; confirmation submits once with progress feedback.");
