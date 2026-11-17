(() => {
    const sidebar = document.querySelector("#sidebar");
    const sidebarToggle = document.querySelector("#sidebar-toggle");
    const menuButton = document.querySelector("#menu-button");
    const drawerClose = document.querySelector("#drawer-close");
    const backdrop = document.querySelector("#drawer-backdrop");
    const desktopQuery = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 1101px)") : null;
    const emitServerEvent = (name, detail) => {
        if (typeof document.dispatchEvent !== "function" || typeof CustomEvent !== "function") return;
        document.dispatchEvent(new CustomEvent(name, detail ? { detail } : undefined));
    };
    let returnFocus = null;

    if (sidebar && sidebarToggle && menuButton && drawerClose && backdrop && desktopQuery) {
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

        const syncSidebarMode = () => {
            closeDrawer();
            setCollapsed(desktopQuery.matches && localStorage.getItem("henvironment-sidebar") === "collapsed");
        };

        syncSidebarMode();

        sidebarToggle.addEventListener("click", () => {
            if (!desktopQuery.matches) return;
            const collapsed = !sidebar.classList.contains("is-collapsed");
            setCollapsed(collapsed);
            sidebar.classList.toggle("just-collapsed", collapsed);
            localStorage.setItem("henvironment-sidebar", collapsed ? "collapsed" : "expanded");
        });

        sidebar.addEventListener("mouseleave", () => {
            sidebar.classList.remove("just-collapsed");
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

        desktopQuery.addEventListener("change", syncSidebarMode);
    }

    const filterForm = document.querySelector("#data-filters");
    const loadingState = document.querySelector("#loading-state");
    const tableStage = document.querySelector("#table-stage");
    const tableHeaderScroll = document.querySelector("#table-header-scroll");
    const tableBodyScroll = document.querySelector("#table-body-scroll");
    let searchTimer;
    let paginationInFlight = false;
    let liveController = null;
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
        liveController?.abort();
        paginationInFlight = true;
        showLoading();
        try {
            const response = await fetch(url, {
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                window.location.href = response.url;
                return;
            }
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

            const currentPage = document.querySelector('.pagination [aria-current="page"]');
            if (currentPage) {
                currentPage.tabIndex = -1;
                currentPage.focus({ preventScroll: true });
            }
        } catch (error) {
            window.location.href = url;
        } finally {
            paginationInFlight = false;
            hideLoading();
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

    const liveInterval = 10000;
    let lastLiveRefresh = Date.now();
    const isFirstPage = () => [null, "", "1"].includes(new URLSearchParams(window.location.search).get("page"));
    const refreshLiveData = async () => {
        if (!filterForm || !tableBodyScroll || document.hidden || !isFirstPage() || paginationInFlight || liveController) return;

        const params = new URLSearchParams(window.location.search);
        params.delete("page");
        params.delete("per_page");
        const url = new URL(filterForm.dataset.liveUrl, window.location.origin);
        url.search = params.toString();
        liveController = new AbortController();

        try {
            const response = await fetch(url, {
                credentials: "same-origin",
                headers: { "Accept": "application/json" },
                signal: liveController.signal,
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                window.location.href = response.url;
                return;
            }
            if (!response.ok) throw new Error(`Live data request failed: ${response.status}`);

            const data = await response.json();
            emitServerEvent("server:request-ok", { source: "data" });
            if (!isFirstPage()) return;
            const tbody = tableBodyScroll.querySelector("tbody");
            const pagination = document.querySelector(".pagination");
            const recordCount = document.querySelector(".record-count");
            if (!tbody || !pagination || !recordCount) return;

            const scrollTop = tableBodyScroll.scrollTop;
            const scrollLeft = tableBodyScroll.scrollLeft;
            const rowsHtml = data.rows_html.trim();
            if (tbody.innerHTML.trim() !== rowsHtml) tbody.innerHTML = rowsHtml;

            const wrapper = document.createElement("div");
            wrapper.innerHTML = data.pagination_html.trim();
            const nextPagination = wrapper.firstElementChild;
            if (nextPagination && pagination.outerHTML !== nextPagination.outerHTML) {
                pagination.replaceWith(nextPagination);
                bindPaginationLinks();
            }
            recordCount.textContent = data.record_count;
            tableBodyScroll.scrollTop = scrollTop;
            tableBodyScroll.scrollLeft = scrollLeft;
        } catch (error) {
            if (error.name !== "AbortError") {
                console.debug("Live data refresh skipped", error);
                emitServerEvent("server:request-failed", { source: "data" });
            }
        } finally {
            liveController = null;
            lastLiveRefresh = Date.now();
        }
    };
    if (filterForm && tableBodyScroll) {
        window.setInterval(refreshLiveData, liveInterval);
        document.addEventListener("server:reconnected", () => refreshLiveData());
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden && Date.now() - lastLiveRefresh >= liveInterval) refreshLiveData();
        });
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
        const readingFilter = document.querySelector("#reading-filter");
        dateOptions.forEach((option, index) => {
            option.addEventListener("click", () => {
                const value = option.dataset.value;
                if (readingFilter) {
                    readingFilter.value = value;
                } else if (dateRange) {
                    dateRange.value = value;
                }
                const labelElem = document.querySelector("#date-range-value");
                if (labelElem) labelElem.textContent = option.textContent;
                dateOptions.forEach((item) => {
                    const selected = item === option;
                    item.classList.toggle("is-selected", selected);
                    item.setAttribute("aria-selected", String(selected));
                });
                closeDateMenu();
                if (customDates) {
                    customDates.hidden = value !== "custom";
                    if (value !== "custom") {
                        customDates.querySelectorAll("input").forEach((input) => { input.value = ""; });
                    }
                }
                submitFilters();
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

    const connectionNotice = document.querySelector("#server-connection-notice");
    if (connectionNotice) {
        const titleElem = document.querySelector("#server-connection-title");
        const messageElem = document.querySelector("#server-connection-message");
        const kickerElem = document.querySelector("#server-connection-kicker-text");
        const retryButton = document.querySelector("#server-connection-retry");
        const healthUrl = connectionNotice.dataset.healthUrl || "/api/health";
        const NORMAL_POLL_MS = 15000;
        const DISCONNECTED_POLL_MS = 5000;
        const SHUTDOWN_POLL_MS = 3000;
        const CONFIRM_DELAY_MS = 1500;
        let consecutiveFailures = 0;
        let isDisconnected = false;
        let isShuttingDown = false;
        let inFlight = false;
        let pollTimer = null;

        const showNotice = (mode, titleText, messageText, kickerText) => {
            connectionNotice.hidden = false;
            connectionNotice.classList.toggle("is-shutdown", mode === "shutdown");
            if (kickerElem && kickerText) kickerElem.textContent = kickerText;
            if (titleElem) titleElem.textContent = titleText;
            if (messageElem) messageElem.textContent = messageText;
        };

        const hideNotice = () => {
            connectionNotice.hidden = true;
            connectionNotice.classList.remove("is-shutdown");
        };

        const scheduleNext = (delayMs) => {
            window.clearTimeout(pollTimer);
            if (document.hidden) return;
            pollTimer = window.setTimeout(checkHealth, delayMs);
        };

        const handleHealthy = () => {
            const wasDown = isDisconnected || isShuttingDown;
            consecutiveFailures = 0;
            isDisconnected = false;
            isShuttingDown = false;
            hideNotice();
            emitServerEvent("server:health-ok");
            if (wasDown) emitServerEvent("server:reconnected");
            scheduleNext(NORMAL_POLL_MS);
        };

        const handleFailure = ({ shuttingDown = false, immediate = false, reasonMessage = "" } = {}) => {
            if (shuttingDown) {
                isShuttingDown = true;
                isDisconnected = false;
                consecutiveFailures = Math.max(consecutiveFailures, 1);
                showNotice(
                    "shutdown",
                    "Server is shutting down",
                    reasonMessage || "Host shutdown was initiated. Live updates have stopped until the server starts again.",
                    "Host shutdown"
                );
                scheduleNext(SHUTDOWN_POLL_MS);
                return;
            }
            const wasShuttingDown = isShuttingDown;
            consecutiveFailures += 1;
            const offline = typeof navigator !== "undefined" && navigator.onLine === false;
            if (offline || immediate || wasShuttingDown || consecutiveFailures >= 2) {
                isShuttingDown = false;
                isDisconnected = true;
                if (offline) {
                    showNotice(
                        "disconnected",
                        "Network disconnected",
                        "Your device is offline. Waiting for network connection…",
                        "Offline"
                    );
                } else if (wasShuttingDown) {
                    showNotice(
                        "disconnected",
                        "Server disconnected",
                        "The server has shut down and is now offline. Retrying automatically…",
                        "Connection lost"
                    );
                } else {
                    showNotice(
                        "disconnected",
                        "Server disconnected",
                        "Cannot reach the Air Quality Monitor server. It may have shut down or lost connection. Retrying automatically…",
                        "Connection lost"
                    );
                }
                scheduleNext(DISCONNECTED_POLL_MS);
                return;
            }
            scheduleNext(CONFIRM_DELAY_MS);
        };

        const checkHealth = async () => {
            if (inFlight) return;
            if (typeof navigator !== "undefined" && navigator.onLine === false) {
                handleFailure({ immediate: true });
                return;
            }
            inFlight = true;
            window.clearTimeout(pollTimer);
            if (retryButton) {
                retryButton.disabled = true;
                retryButton.textContent = "Checking…";
            }
            const controller = typeof AbortController === "function" ? new AbortController() : null;
            const timeout = controller ? window.setTimeout(() => controller.abort(), 5000) : null;
            try {
                const response = await fetch(healthUrl, {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                    cache: "no-store",
                    signal: controller?.signal,
                });
                const payload = await response.json().catch(() => null);
                if (response.ok && payload && payload.status === "ok" && payload.shutting_down !== true) {
                    handleHealthy();
                } else if (payload?.shutting_down === true || payload?.status === "shutting_down") {
                    handleFailure({ shuttingDown: true, reasonMessage: payload.message });
                } else {
                    handleFailure();
                }
            } catch (_) {
                handleFailure();
            } finally {
                if (timeout !== null) window.clearTimeout(timeout);
                inFlight = false;
                if (retryButton) {
                    retryButton.disabled = false;
                    retryButton.textContent = "Retry now";
                }
            }
        };

        retryButton?.addEventListener("click", () => checkHealth());
        document.addEventListener("server:shutting-down", (event) => {
            handleFailure({
                shuttingDown: true,
                immediate: true,
                reasonMessage: event?.detail?.message,
            });
        });
        document.addEventListener("server:request-failed", () => {
            if (!isDisconnected) checkHealth();
        });
        document.addEventListener("server:request-ok", () => {
            if (isShuttingDown) checkHealth();
            else if (isDisconnected) handleHealthy();
        });
        window.addEventListener("offline", () => handleFailure({ immediate: true }));
        window.addEventListener("online", () => checkHealth());
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) checkHealth();
            else window.clearTimeout(pollTimer);
        });
        scheduleNext(NORMAL_POLL_MS);
    }
})();
