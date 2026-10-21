(() => {
    const form = document.querySelector("#delete-readings-form");
    const button = document.querySelector("#delete-all-readings");
    if (!form || !button) return;
    button.disabled = false;
    form.addEventListener("submit", (event) => {
        if (button.disabled) {
            event.preventDefault();
            return;
        }
        const confirmed = window.confirm(
            "Delete ALL sensor readings from every device and page, including readings hidden by filters?\n\n" +
            "This cannot be undone. The next new reading will start at ID 1. Dashboard accounts will be kept.\n\n" +
            "Pause the ESP32 and stop any simulator first if you want the table to stay empty.\n\n" +
            "Delete all readings?"
        );
        if (!confirmed) {
            event.preventDefault();
            return;
        }
        form.elements.confirmation.value = "delete_all";
        button.disabled = true;
        button.textContent = "Deleting…";
        button.setAttribute("aria-busy", "true");
    });
})();
