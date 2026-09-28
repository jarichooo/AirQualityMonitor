(() => {
    const sidebar = document.querySelector("#sidebar");
    const sidebarToggle = document.querySelector("#sidebar-toggle");
    const menuButton = document.querySelector("#menu-button");
    const drawerClose = document.querySelector("#drawer-close");
    const backdrop = document.querySelector("#drawer-backdrop");
    const desktopQuery = window.matchMedia("(min-width: 901px)");
    let returnFocus = null;

    const setCollapsed = (collapsed) => {
        sidebar.classList.toggle("is-collapsed", collapsed);
        document.body.classList.toggle("sidebar-collapsed", collapsed);
        sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
        sidebarToggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Minimize sidebar");
        sidebarToggle.dataset.tooltip = collapsed ? "Expand navigation" : "Collapse navigation";
    };

    const closeDrawer = () => {
        sidebar.classList.remove("drawer-open");
        document.body.classList.remove("drawer-open");
        backdrop.hidden = true;
        menuButton.setAttribute("aria-expanded", "false");
        if (returnFocus) returnFocus.focus();
    };

    const openDrawer = () => {
        returnFocus = document.activeElement;
        sidebar.classList.add("drawer-open");
        document.body.classList.add("drawer-open");
        backdrop.hidden = false;
        menuButton.setAttribute("aria-expanded", "true");
        drawerClose.focus();
    };

    setCollapsed(localStorage.getItem("henvironment-sidebar") === "collapsed");

    sidebarToggle.addEventListener("click", () => {
        if (!desktopQuery.matches) return;
        const collapsed = !sidebar.classList.contains("is-collapsed");
        setCollapsed(collapsed);
        localStorage.setItem("henvironment-sidebar", collapsed ? "collapsed" : "expanded");
    });

    menuButton.addEventListener("click", openDrawer);
    drawerClose.addEventListener("click", closeDrawer);
    backdrop.addEventListener("click", closeDrawer);
    sidebar.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => {
        if (!desktopQuery.matches) closeDrawer();
    }));

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            if (sidebar.classList.contains("drawer-open")) closeDrawer();
        }
        if (event.key === "Tab" && sidebar.classList.contains("drawer-open")) {
            const focusable = [...sidebar.querySelectorAll("button, a[href]")].filter((element) => !element.hidden);
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });

    desktopQuery.addEventListener("change", () => {
        closeDrawer();
        setCollapsed(localStorage.getItem("henvironment-sidebar") === "collapsed");
    });

    const filterForm = document.querySelector("#data-filters");
    const loadingState = document.querySelector("#loading-state");
    const tableStage = document.querySelector("#table-stage");
    const tableHeaderScroll = document.querySelector("#table-header-scroll");
    const tableBodyScroll = document.querySelector("#table-body-scroll");
    let searchTimer;
    const showLoading = () => {
        if (loadingState) loadingState.hidden = false;
        if (tableStage) tableStage.setAttribute("aria-busy", "true");
    };
    const hideLoading = () => {
        if (loadingState) loadingState.hidden = true;
        if (tableStage) tableStage.removeAttribute("aria-busy");
    };
    if (filterForm) filterForm.addEventListener("submit", () => {
        window.clearTimeout(searchTimer);
        showLoading();
    });

    const loadPaginationPage = async (url, pushHistory = true) => {
        showLoading();
        try {
            const response = await fetch(url, {
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            });
            if (!response.ok) throw new Error(`Pagination request failed: ${response.status}`);

            const nextDocument = new DOMParser().parseFromString(await response.text(), "text/html");
            const currentBody = document.querySelector("#table-body-scroll");
            const nextBody = nextDocument.querySelector("#table-body-scroll");
            const currentTbody = currentBody?.querySelector("tbody");
            const nextTbody = nextBody?.querySelector("tbody");
            const currentPagination = document.querySelector(".pagination");
            const nextPagination = nextDocument.querySelector(".pagination");
            if (!currentBody || !currentTbody || !nextTbody || !currentPagination || !nextPagination) {
                throw new Error("Pagination response is missing the table body");
            }

            currentTbody.replaceWith(document.importNode(nextTbody, true));
            currentPagination.replaceWith(document.importNode(nextPagination, true));
            currentBody.scrollTop = 0;
            if (pushHistory) window.history.pushState({}, "", url);
            bindPaginationLinks();
            hideLoading();

            const currentPage = document.querySelector('.pagination [aria-current="page"]');
            if (currentPage) {
                currentPage.tabIndex = -1;
                currentPage.focus({ preventScroll: true });
            }
        } catch (error) {
            hideLoading();
            window.location.href = url;
        }
    };
    const bindPaginationLinks = () => {
        document.querySelectorAll(".pagination-link").forEach((link) => link.addEventListener("click", (event) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            loadPaginationPage(link.href);
        }));
    };
    bindPaginationLinks();
    window.addEventListener("popstate", () => {
        if (window.location.pathname === "/data") loadPaginationPage(window.location.href, false);
    });

    if (tableHeaderScroll && tableBodyScroll) {
        tableBodyScroll.addEventListener("scroll", () => {
            tableHeaderScroll.scrollLeft = tableBodyScroll.scrollLeft;
        }, { passive: true });
    }

    const dateRange = document.querySelector("#date-range");
    const customDates = document.querySelector("#custom-dates");
    const customSelect = document.querySelector("[data-custom-select]");
    const dateTrigger = document.querySelector("#date-range-trigger");
    const dateOptions = [...document.querySelectorAll(".custom-select-option")];
    const closeDateMenu = (restoreFocus = false) => {
        if (!customSelect || !dateTrigger) return;
        customSelect.classList.remove("is-open");
        dateTrigger.setAttribute("aria-expanded", "false");
        if (restoreFocus) dateTrigger.focus();
    };
    const openDateMenu = (focusSelected = false) => {
        if (!customSelect || !dateTrigger) return;
        customSelect.classList.add("is-open");
        dateTrigger.setAttribute("aria-expanded", "true");
        if (focusSelected) {
            (dateOptions.find((option) => option.getAttribute("aria-selected") === "true") || dateOptions[0]).focus();
        }
    };
    const submitFilters = () => {
        if (filterForm) filterForm.requestSubmit();
    };
    if (dateRange && customDates && customSelect && dateTrigger) {
        dateTrigger.addEventListener("click", () => {
            if (customSelect.classList.contains("is-open")) closeDateMenu();
            else openDateMenu();
        });
        dateTrigger.addEventListener("keydown", (event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                openDateMenu(true);
            } else if (event.key === "Escape") {
                closeDateMenu();
            }
        });
        dateOptions.forEach((option, index) => {
            option.addEventListener("click", () => {
                const value = option.dataset.value;
                dateRange.value = value;
                document.querySelector("#date-range-value").textContent = option.textContent;
                dateOptions.forEach((item) => {
                    const selected = item === option;
                    item.classList.toggle("is-selected", selected);
                    item.setAttribute("aria-selected", String(selected));
                });
                closeDateMenu();
                customDates.hidden = value !== "custom";
                if (value !== "custom") {
                    customDates.querySelectorAll("input").forEach((input) => { input.value = ""; });
                    submitFilters();
                }
                else if (customDates.querySelectorAll("input")[0].value && customDates.querySelectorAll("input")[1].value) submitFilters();
            });
            option.addEventListener("keydown", (event) => {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    const offset = event.key === "ArrowDown" ? 1 : -1;
                    dateOptions[(index + offset + dateOptions.length) % dateOptions.length].focus();
                } else if (event.key === "Escape") {
                    event.preventDefault();
                    closeDateMenu(true);
                } else if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    option.click();
                }
            });
        });
        customDates.querySelectorAll("input").forEach((input) => input.addEventListener("change", () => {
            const dates = [...customDates.querySelectorAll("input")];
            if (dateRange.value === "custom" && dates.every((date) => date.value)) submitFilters();
        }));
        document.addEventListener("click", (event) => {
            if (!customSelect.contains(event.target)) closeDateMenu();
        });
    }

    const searchInput = filterForm && filterForm.querySelector('input[name="q"]');
    if (searchInput) {
        searchInput.addEventListener("input", () => {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(submitFilters, 350);
        });
    }

    const exportButton = document.querySelector("#export-button");
    if (exportButton) {
        exportButton.addEventListener("click", (event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            const label = exportButton.querySelector("span");
            label.textContent = "Preparing…";
            exportButton.setAttribute("aria-busy", "true");
            window.setTimeout(() => {
                label.textContent = exportButton.dataset.defaultLabel;
                exportButton.removeAttribute("aria-busy");
            }, 1800);
        });
    }
})();
