(() => {
    const dialog = document.querySelector("#delete-readings-dialog");
    const form = document.querySelector("#delete-readings-form");
    const openButton = document.querySelector("#delete-all-readings");
    const cancelButton = document.querySelector("#cancel-delete-readings");
    const closeButton = document.querySelector("#close-delete-readings");
    const submitButton = document.querySelector("#confirm-delete-readings");
    const mode = document.querySelector("#delete-mode");
    const cutoffPanel = document.querySelector("#delete-cutoff-panel");
    const fromField = document.querySelector("#delete-from-field");
    const from = document.querySelector("#delete-from");
    const scopeField = document.querySelector("#delete-scope-field");
    const scope = document.querySelector("#delete-scope");
    const scopeHint = document.querySelector("#delete-scope-hint");
    const summaryCard = document.querySelector("#delete-summary-card");
    const scopeSummary = document.querySelector("#delete-scope-summary");
    const idBehavior = document.querySelector("#delete-id-behavior");
    const modeCards = typeof document.querySelectorAll === "function"
        ? Array.from(document.querySelectorAll("[data-delete-mode]"))
        : [];
    if (!dialog || !form || !openButton || !cancelButton || !submitButton ||
        !mode || !from || !scope || !scopeSummary) return;

    let submitting = false;
    const allowedModes = new Set(["from_timestamp", "all_simulated", "incomplete", "all"]);
    const allowedScopes = new Set(["simulated", "all"]);
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const submitLabels = {
        from_timestamp: "Delete matching readings",
        all_simulated: "Delete simulation readings",
        incomplete: "Delete incomplete records",
        all: "Delete all readings",
    };

    const parsePhtTimestamp = (value) => {
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value || "");
        if (!match) return null;
        const year = Number(match[1]);
        const month = Number(match[2]);
        const day = Number(match[3]);
        const hour = Number(match[4]);
        const minute = Number(match[5]);
        const second = match[6] === undefined ? 0 : Number(match[6]);
        const instant = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
        if (
            instant.getUTCFullYear() !== year ||
            instant.getUTCMonth() !== month - 1 ||
            instant.getUTCDate() !== day ||
            instant.getUTCHours() !== hour ||
            instant.getUTCMinutes() !== minute ||
            instant.getUTCSeconds() !== second
        ) {
            return null;
        }
        const period = hour >= 12 ? "PM" : "AM";
        const hour12 = String(hour % 12 || 12).padStart(2, "0");
        const minPad = String(minute).padStart(2, "0");
        const secPad = String(second).padStart(2, "0");
        return {
            display: `${months[month - 1]} ${day}, ${year}, ${hour12}:${minPad}:${secPad} ${period} PHT`,
        };
    };

    const syncModeCards = () => {
        for (const card of modeCards) {
            const active = card.getAttribute("data-delete-mode") === mode.value;
            card.setAttribute("aria-pressed", active ? "true" : "false");
            card.classList?.toggle("is-selected", active);
        }
    };

    const updateSummary = () => {
        const isTimestampMode = mode.value === "from_timestamp";
        const parsedCutoff = isTimestampMode ? parsePhtTimestamp(from.value) : null;
        const timeText = !isTimestampMode
            ? "Across all timestamps"
            : parsedCutoff
                ? `From ${parsedCutoff.display} (UTC+8) onward`
                : "From the selected PHT (UTC+8) time onward";
        const sourceText = mode.value === "incomplete"
            ? "every record with at least one missing sensor reading will be deleted."
            : scope.value === "simulated"
                ? "only rows from the known simulation devices will be deleted."
                : "rows from every device, including the imported dataset, will be deleted.";
        scopeSummary.textContent = `${timeText}, ${sourceText}`;

        const highImpact = mode.value === "all" || (isTimestampMode && scope.value === "all");
        if (summaryCard) summaryCard.setAttribute("data-impact", highImpact ? "danger" : "scoped");
        if (scopeHint) {
            scopeHint.textContent = scope.value === "simulated"
                ? "Targets known simulation devices only; keeps real sensor and imported rows."
                : "Targets every physical sensor node, simulator run, and imported dataset.";
        }
        if (idBehavior) {
            idBehavior.textContent = mode.value === "all"
                ? "The reading ID sequence resets so the next reading ID is 1."
                : "Retained row IDs stay unchanged; the next reading continues after the highest retained ID.";
        }
        if (!submitting) {
            submitButton.textContent = submitLabels[mode.value] || "Delete selected readings";
        }
    };

    const updateControls = () => {
        const allTime = mode.value === "all" || mode.value === "all_simulated" || mode.value === "incomplete";
        from.disabled = allTime;
        from.required = !allTime;
        from.setCustomValidity("");
        if (cutoffPanel) cutoffPanel.hidden = allTime;
        if (fromField) fromField.hidden = allTime;
        if (scopeField) scopeField.hidden = allTime;

        if (mode.value === "all" || mode.value === "incomplete") {
            scope.value = "all";
            scope.disabled = true;
        } else if (mode.value === "all_simulated") {
            scope.value = "simulated";
            scope.disabled = true;
        } else if (scope.disabled) {
            scope.value = "simulated";
            scope.disabled = false;
        }
        syncModeCards();
        updateSummary();
    };

    openButton.disabled = false;
    submitButton.disabled = false;
    updateControls();

    const closeDialog = () => {
        if (!submitting) dialog.close();
    };

    openButton.addEventListener("click", () => dialog.showModal());
    cancelButton.addEventListener("click", closeDialog);
    if (closeButton) closeButton.addEventListener("click", closeDialog);
    dialog.addEventListener("click", (event) => {
        if (event && event.target === dialog) closeDialog();
    });
    for (const card of modeCards) {
        card.addEventListener("click", () => {
            if (submitting) return;
            const nextMode = card.getAttribute("data-delete-mode");
            if (!allowedModes.has(nextMode) || mode.value === nextMode) return;
            mode.value = nextMode;
            updateControls();
        });
    }
    mode.addEventListener("change", updateControls);
    scope.addEventListener("change", updateControls);
    from.addEventListener("input", () => {
        from.setCustomValidity("");
        updateSummary();
    });
    from.addEventListener("change", updateSummary);

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (submitting || submitButton.disabled) return;
        form.elements.confirmation.value = "";

        if (!allowedModes.has(mode.value) || !allowedScopes.has(scope.value)) return;
        const parsedCutoff = mode.value === "from_timestamp" ? parsePhtTimestamp(from.value) : null;
        if (mode.value === "from_timestamp" && !parsedCutoff) {
            from.setCustomValidity("Choose a valid inclusive PHT (UTC+8) start time.");
            from.reportValidity();
            return;
        }

        const timeText = (mode.value === "all" || mode.value === "all_simulated" || mode.value === "incomplete")
            ? "ALL timestamps"
            : `timestamps at or after ${parsedCutoff.display} (UTC+8)`;
        const sourceText = mode.value === "incomplete"
            ? "devices with fewer than 120 minute readings"
            : (mode.value === "all_simulated" || scope.value === "simulated")
                ? "the known simulation devices"
                : "EVERY device, including the imported dataset";
        const sequenceNote = mode.value === "all"
            ? "All saved forecasts will also be cleared, and the next reading ID will reset to 1."
            : "Retained row IDs will not change, and the next reading will follow the highest retained ID.";
        const confirmHeadline = mode.value === "incomplete"
            ? "Delete every record that has one or more missing sensor readings across ALL timestamps?"
            : `Delete readings from ${sourceText} for ${timeText}?`;
        const confirmed = window.confirm(
            `${confirmHeadline}\n\n` +
            `This ignores the current table filters and cannot be undone. ${sequenceNote}\n\n` +
            "Make sure active simulations and predictions are stopped before continuing."
        );
        if (!confirmed) return;

        form.elements.confirmation.value = mode.value === "all"
            ? "delete_all"
            : mode.value === "all_simulated"
                ? "delete_all_simulated"
                : mode.value === "incomplete"
                    ? "delete_incomplete"
                    : "delete_from_timestamp";
        submitting = true;
        submitButton.disabled = true;
        submitButton.textContent = "Deleting…";
        submitButton.setAttribute("aria-busy", "true");
        form.submit();
    });
})();
