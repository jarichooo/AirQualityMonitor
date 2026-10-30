(() => {
    const dialog = document.querySelector("#delete-readings-dialog");
    const form = document.querySelector("#delete-readings-form");
    const openButton = document.querySelector("#delete-all-readings");
    const cancelButton = document.querySelector("#cancel-delete-readings");
    const submitButton = document.querySelector("#confirm-delete-readings");
    const mode = document.querySelector("#delete-mode");
    const from = document.querySelector("#delete-from");
    const scope = document.querySelector("#delete-scope");
    const scopeSummary = document.querySelector("#delete-scope-summary");
    if (!dialog || !form || !openButton || !cancelButton || !submitButton ||
        !mode || !from || !scope || !scopeSummary) return;

    let submitting = false;
    const allowedModes = new Set(["from_timestamp", "all"]);
    const allowedScopes = new Set(["simulated", "all"]);

    const validUtcTimestamp = (value) => {
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
        if (!match) return false;
        const parts = match.slice(1).map(Number);
        const instant = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4], parts[5] || 0));
        return instant.getUTCFullYear() === parts[0] && instant.getUTCMonth() === parts[1] - 1 &&
            instant.getUTCDate() === parts[2] && instant.getUTCHours() === parts[3] &&
            instant.getUTCMinutes() === parts[4] && instant.getUTCSeconds() === (parts[5] || 0);
    };

    const updateControls = () => {
        const allTime = mode.value === "all";
        from.disabled = allTime;
        from.required = !allTime;
        from.setCustomValidity("");
        if (allTime) {
            scope.value = "all";
            scope.disabled = true;
        } else if (scope.disabled) {
            scope.value = "simulated";
            scope.disabled = false;
        }
        const timeText = allTime ? "Across all timestamps" : "From the selected UTC time onward";
        const sourceText = scope.value === "simulated"
            ? "only rows from the known simulation devices will be deleted."
            : "rows from every device, including the imported dataset, will be deleted.";
        scopeSummary.textContent = `${timeText}, ${sourceText}`;
    };

    openButton.disabled = false;
    submitButton.disabled = false;
    updateControls();

    openButton.addEventListener("click", () => dialog.showModal());
    cancelButton.addEventListener("click", () => dialog.close());
    mode.addEventListener("change", updateControls);
    scope.addEventListener("change", updateControls);
    from.addEventListener("input", () => from.setCustomValidity(""));

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (submitting || submitButton.disabled) return;
        form.elements.confirmation.value = "";

        if (!allowedModes.has(mode.value) || !allowedScopes.has(scope.value)) return;
        if (mode.value === "from_timestamp" && !validUtcTimestamp(from.value)) {
            from.setCustomValidity("Choose a valid inclusive UTC start time.");
            from.reportValidity();
            return;
        }

        const timeText = mode.value === "all"
            ? "ALL timestamps"
            : `timestamps at or after ${from.value.replace("T", " ")} UTC`;
        const sourceText = scope.value === "simulated"
            ? "the known simulation devices"
            : "EVERY device, including the imported dataset";
        const confirmed = window.confirm(
            `Delete readings from ${sourceText} for ${timeText}?\n\n` +
            "This ignores the current table filters and cannot be undone. Retained row IDs will not change, and the next reading will follow the highest retained ID.\n\n" +
            "Pause data collection, stop simulations, and wait for predictions to finish before continuing."
        );
        if (!confirmed) return;

        form.elements.confirmation.value = mode.value === "all" ? "delete_all" : "delete_from_timestamp";
        submitting = true;
        submitButton.disabled = true;
        submitButton.textContent = "Deleting…";
        submitButton.setAttribute("aria-busy", "true");
        form.submit();
    });
})();
