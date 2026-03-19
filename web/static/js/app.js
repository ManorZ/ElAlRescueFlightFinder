/* ============================================================
   El Al Rescue Flight Finder - Dashboard JavaScript
   ============================================================ */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let allFlights = [];
let destinations = [];
let sortColumn = 'flight_date';
let sortDirection = 'asc';
let refreshCountdown = 60;
let countdownInterval = null;
let autoRefreshInterval = null;
let selectedOrigins = [];  // multi-select state

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function timeAgo(dateString) {
    if (!dateString) return '--';
    const now = new Date();
    const then = new Date(dateString);
    const diffMs = now - then;

    if (isNaN(diffMs)) return dateString;

    const seconds = Math.floor(diffMs / 1000);
    if (seconds < 60) return 'just now';

    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + (minutes === 1 ? ' minute ago' : ' minutes ago');

    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + (hours === 1 ? ' hour ago' : ' hours ago');

    const days = Math.floor(hours / 24);
    return days + (days === 1 ? ' day ago' : ' days ago');
}

function formatDate(dateString) {
    if (!dateString) return '--';
    try {
        const d = new Date(dateString);
        if (isNaN(d.getTime())) return dateString;
        return d.toLocaleDateString('en-GB', {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            year: 'numeric'
        });
    } catch {
        return dateString;
    }
}

function isNewFlight(firstSeenAt) {
    if (!firstSeenAt) return false;
    const seen = new Date(firstSeenAt);
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000);
    return seen > oneHourAgo;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiFetch(url, options) {
    try {
        const res = await fetch(url, options);
        setConnectionStatus(true);
        if (!res.ok) {
            console.error('API error:', res.status, url);
            return null;
        }
        if (res.status === 204) return true;
        return await res.json();
    } catch (err) {
        console.error('Network error:', err);
        setConnectionStatus(false);
        return null;
    }
}

function setConnectionStatus(connected) {
    const dot = document.getElementById('connection-status');
    if (connected) {
        dot.className = 'connection-dot connected';
        dot.title = 'Connected';
    } else {
        dot.className = 'connection-dot disconnected';
        dot.title = 'Disconnected';
    }
}

// ---------------------------------------------------------------------------
// Multi-select Origin Filter
// ---------------------------------------------------------------------------

function initOriginMultiSelect() {
    const container = document.getElementById('origin-multi-select');
    const btn = document.getElementById('origin-multi-btn');
    const dropdown = document.getElementById('origin-multi-dropdown');

    btn.addEventListener('click', function (e) {
        e.stopPropagation();
        container.classList.toggle('open');
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', function (e) {
        if (!container.contains(e.target)) {
            container.classList.remove('open');
        }
    });

    // Prevent dropdown clicks from closing it
    dropdown.addEventListener('click', function (e) {
        e.stopPropagation();
    });
}

function populateOriginMultiSelect(origins) {
    // origins = [{code, label}, ...]
    const list = document.getElementById('origin-multi-list');
    list.innerHTML = '';

    origins.forEach(function (o) {
        var lbl = document.createElement('label');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = o.code;
        cb.checked = selectedOrigins.indexOf(o.code) !== -1;
        cb.addEventListener('change', onOriginCheckboxChange);
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(' ' + o.label));
        list.appendChild(lbl);
    });
}

function onOriginCheckboxChange() {
    var checkboxes = document.querySelectorAll('#origin-multi-list input[type="checkbox"]');
    selectedOrigins = [];
    checkboxes.forEach(function (cb) {
        if (cb.checked) selectedOrigins.push(cb.value);
    });
    updateOriginButtonText();
    // Update hidden input
    document.getElementById('filter-origin').value = selectedOrigins.join(',');
}

function updateOriginButtonText() {
    var btn = document.getElementById('origin-multi-btn');
    if (selectedOrigins.length === 0) {
        btn.textContent = 'All Origins';
    } else if (selectedOrigins.length <= 3) {
        // Show city names for selected codes
        var names = selectedOrigins.map(function (code) {
            var found = destinations.find(function (d) { return d.code === code; });
            return found ? found.city_name : code;
        });
        btn.textContent = names.join(', ');
    } else {
        btn.textContent = selectedOrigins.length + ' origins selected';
    }
}

// ---------------------------------------------------------------------------
// Flights
// ---------------------------------------------------------------------------

async function loadFlights() {
    const params = buildFilterParams();
    const queryString = params.toString();
    const url = '/api/flights' + (queryString ? '?' + queryString : '');
    const data = await apiFetch(url);
    if (data !== null && Array.isArray(data)) {
        allFlights = data;
        sortFlights();
        renderFlights(allFlights);
        // After loading flights, update the filter dropdown with origins from flights
        updateFilterOrigins();
    }
}

function buildFilterParams() {
    const params = new URLSearchParams();
    const origin = document.getElementById('filter-origin').value;
    const dateFrom = document.getElementById('filter-date-from').value;
    const dateTo = document.getElementById('filter-date-to').value;
    const availableOnly = document.getElementById('filter-available-only').checked;

    if (origin) params.set('origin', origin);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (availableOnly) params.set('available_only', '1');

    return params;
}

function updateFilterOrigins() {
    // Derive unique origins from the full (unfiltered) flights data
    // We call the API without origin filter to get all origins
    // But to avoid extra calls, derive from allFlights when no origin filter is set,
    // or from a cached list
    // Actually, let's just populate from the destinations that have is_recovery_flight_origin=1
    // which is already in our destinations array
    var originList = [];
    var seen = {};

    // Use destinations with is_recovery_flight_origin = 1 (active flight origins)
    destinations.forEach(function (d) {
        if (d.is_recovery_flight_origin && d.code && !seen[d.code]) {
            seen[d.code] = true;
            originList.push({
                code: d.code,
                label: d.city_name + ' (' + d.code + ')'
            });
        }
    });

    // Also add any origins from flights that might not be in destinations yet
    allFlights.forEach(function (f) {
        if (f.origin_code && !seen[f.origin_code]) {
            seen[f.origin_code] = true;
            originList.push({
                code: f.origin_code,
                label: (f.origin_city || f.origin_code) + ' (' + f.origin_code + ')'
            });
        }
    });

    originList.sort(function (a, b) {
        return a.label.localeCompare(b.label);
    });

    populateOriginMultiSelect(originList);
}

function renderFlights(flights) {
    const tbody = document.getElementById('flight-table-body');

    if (!flights || flights.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No flights found</td></tr>';
        return;
    }

    let html = '';
    for (const f of flights) {
        const seatsAvail = f.seats_available != null ? f.seats_available : null;
        const isAvailable = seatsAvail !== null && seatsAvail > 0;
        const isSoldOut = seatsAvail !== null && seatsAvail === 0;
        const isNew = isNewFlight(f.first_seen_at) || f.is_new === 1;

        let rowClass = '';
        if (isNew) {
            rowClass = 'row-new';
        } else if (isAvailable) {
            rowClass = 'row-available';
        } else if (isSoldOut) {
            rowClass = 'row-sold-out';
        }

        const seatsClass = isAvailable ? 'seats-available' : (isSoldOut ? 'seats-sold-out' : '');
        const seatsText = seatsAvail !== null ? seatsAvail : '--';

        html += '<tr class="' + rowClass + '">';
        html += '<td>' + escapeHtml(f.origin_city || '--') + '</td>';
        html += '<td>' + escapeHtml(f.origin_country || '--') + '</td>';
        html += '<td><strong>' + escapeHtml(f.flight_number || '--') + '</strong></td>';
        html += '<td>' + escapeHtml(f.flight_time || '--') + '</td>';
        html += '<td>' + formatDate(f.flight_date) + '</td>';
        html += '<td class="' + seatsClass + '">' + seatsText + '</td>';
        html += '<td>' + timeAgo(f.first_seen_at) + '</td>';
        html += '</tr>';
    }

    tbody.innerHTML = html;
}

function sortFlights() {
    const col = sortColumn;
    const dir = sortDirection === 'asc' ? 1 : -1;

    allFlights.sort(function (a, b) {
        let va = a[col];
        let vb = b[col];

        if (va == null) va = '';
        if (vb == null) vb = '';

        if (col === 'seats_available') {
            va = Number(va) || 0;
            vb = Number(vb) || 0;
            return (va - vb) * dir;
        }

        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();

        if (va < vb) return -1 * dir;
        if (va > vb) return 1 * dir;
        return 0;
    });
}

function handleSort(e) {
    const th = e.target.closest('th[data-sort]');
    if (!th) return;

    const col = th.getAttribute('data-sort');

    // Toggle direction if same column, otherwise default to asc
    if (sortColumn === col) {
        sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        sortColumn = col;
        sortDirection = 'asc';
    }

    // Update header classes
    document.querySelectorAll('.flight-table th').forEach(function (h) {
        h.classList.remove('sort-asc', 'sort-desc');
    });
    th.classList.add(sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');

    sortFlights();
    renderFlights(allFlights);
}

function applyFilters() {
    loadFlights();
}

function clearFilters() {
    selectedOrigins = [];
    document.getElementById('filter-origin').value = '';
    updateOriginButtonText();
    // Uncheck all checkboxes
    document.querySelectorAll('#origin-multi-list input[type="checkbox"]').forEach(function (cb) {
        cb.checked = false;
    });
    document.getElementById('filter-date-from').value = '';
    document.getElementById('filter-date-to').value = '';
    document.getElementById('filter-available-only').checked = false;
    loadFlights();
}

// ---------------------------------------------------------------------------
// Destinations (for filter dropdown and alert dropdown)
// ---------------------------------------------------------------------------

async function loadDestinations() {
    // Load all destinations (including those without current flights) for the alert dropdown
    const allDests = await apiFetch('/api/all-destinations');

    if (allDests && Array.isArray(allDests)) {
        destinations = allDests;
    } else {
        // Fallback to regular destinations endpoint
        const data = await apiFetch('/api/destinations');
        if (data && Array.isArray(data)) {
            destinations = data;
        } else {
            return;
        }
    }

    // Build a set of origin codes that have current flights
    var activeOriginCodes = new Set();
    allFlights.forEach(function (f) {
        if (f.origin_code) activeOriginCodes.add(f.origin_code);
    });
    // Also include destinations marked as recovery flight origins
    destinations.forEach(function (d) {
        if (d.is_recovery_flight_origin) activeOriginCodes.add(d.code);
    });

    // Populate the filter multi-select (only origins with flights)
    updateFilterOrigins();

    // Populate alert destination dropdown with ALL destinations
    populateAlertDropdown(activeOriginCodes);
}

function populateAlertDropdown(activeOriginCodes) {
    const alertSelect = document.getElementById('alert-destination');
    alertSelect.innerHTML = '<option value="">Select origin city...</option>';

    // Sort destinations alphabetically by city name
    var sorted = destinations.slice().sort(function (a, b) {
        // Active origins first, then alphabetical
        var aActive = activeOriginCodes.has(a.code);
        var bActive = activeOriginCodes.has(b.code);
        if (aActive && !bActive) return -1;
        if (!aActive && bActive) return 1;
        return (a.city_name || '').localeCompare(b.city_name || '');
    });

    var seen = new Set();
    sorted.forEach(function (d) {
        if (!d.code || seen.has(d.code)) return;
        seen.add(d.code);

        var opt = document.createElement('option');
        opt.value = d.code + '|' + (d.city_name || '');
        var label = d.city_name + ' (' + d.code + ')';
        if (!activeOriginCodes.has(d.code)) {
            label += ' (no current flights)';
        }
        opt.textContent = label;
        alertSelect.appendChild(opt);
    });
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

async function loadAlerts() {
    const data = await apiFetch('/api/alerts');
    if (!data || !Array.isArray(data)) return;

    const list = document.getElementById('alert-list');
    const countBadge = document.getElementById('alert-count');
    countBadge.textContent = data.length;

    if (data.length === 0) {
        list.innerHTML = '<p class="empty-message">No alerts configured.</p>';
        return;
    }

    let html = '';
    for (const a of data) {
        const isActive = a.is_active === 1 || a.is_active === true;
        const cardClass = isActive ? 'alert-card' : 'alert-card inactive';
        const toggleLabel = isActive ? 'Active' : 'Paused';
        const toggleClass = isActive ? 'toggle-btn active' : 'toggle-btn';

        html += '<div class="' + cardClass + '">';
        html += '  <div class="alert-card-header">';
        html += '    <span class="alert-city">' + escapeHtml(a.destination_city || a.destination_code) + '</span>';
        html += '    <div class="alert-card-actions">';
        html += '      <button class="' + toggleClass + '" onclick="toggleAlert(' + a.id + ', ' + (isActive ? 1 : 0) + ')">' + toggleLabel + '</button>';
        html += '      <button class="btn btn-danger btn-sm" onclick="deleteAlert(' + a.id + ')">Delete</button>';
        html += '    </div>';
        html += '  </div>';
        html += '  <div class="alert-detail">' + formatDate(a.trigger_date) + ' &middot; ' + escapeHtml(a.email_address) + '</div>';
        html += '</div>';
    }

    list.innerHTML = html;
}

async function addAlert(e) {
    e.preventDefault();

    const destValue = document.getElementById('alert-destination').value;
    if (!destValue) return;

    const parts = destValue.split('|');
    const destinationCode = parts[0];
    const destinationCity = parts[1] || '';

    const triggerDate = document.getElementById('alert-date').value;
    const emailAddress = document.getElementById('alert-email').value;

    if (!triggerDate || !emailAddress) return;

    const body = {
        destination_code: destinationCode,
        destination_city: destinationCity,
        trigger_date: triggerDate,
        email_address: emailAddress
    };

    const result = await apiFetch('/api/alerts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    if (result) {
        // Clear form
        document.getElementById('alert-destination').value = '';
        document.getElementById('alert-date').value = '';
        document.getElementById('alert-email').value = '';
        loadAlerts();
    }
}

async function deleteAlert(id) {
    const result = await apiFetch('/api/alerts/' + id, { method: 'DELETE' });
    if (result !== null) {
        loadAlerts();
    }
}

async function toggleAlert(id, currentState) {
    const newState = currentState === 1 ? 0 : 1;
    const result = await apiFetch('/api/alerts/' + id, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: newState })
    });
    if (result) {
        loadAlerts();
    }
}

// ---------------------------------------------------------------------------
// News
// ---------------------------------------------------------------------------

async function loadNews() {
    const data = await apiFetch('/api/news');
    if (!data || data.error) {
        document.getElementById('non-operational-badges').innerHTML =
            '<span class="empty-message">No data available</span>';
        document.getElementById('recovery-badges').innerHTML =
            '<span class="empty-message">No data available</span>';
        document.getElementById('news-timestamp').textContent = '';
        return;
    }

    // Timestamp
    if (data.captured_at) {
        document.getElementById('news-timestamp').textContent =
            'Updated: ' + timeAgo(data.captured_at);
    }

    // Non-operational destinations
    const nonOp = Array.isArray(data.non_operational_destinations)
        ? data.non_operational_destinations : [];
    renderBadges('non-operational-badges', nonOp, 'badge-red');

    // Recovery flight origins
    const recovery = Array.isArray(data.recovery_flight_origins)
        ? data.recovery_flight_origins : [];
    renderBadges('recovery-badges', recovery, 'badge-green');
}

function renderBadges(containerId, items, colorClass) {
    const container = document.getElementById(containerId);
    if (!items || items.length === 0) {
        container.innerHTML = '<span class="empty-message">None</span>';
        return;
    }

    let html = '';
    for (const item of items) {
        const text = typeof item === 'string' ? item : (item.city_name || item.name || item.code || String(item));
        html += '<span class="badge ' + colorClass + '">' + escapeHtml(text) + '</span>';
    }
    container.innerHTML = html;
}

function toggleNews() {
    const content = document.getElementById('news-content');
    const chevron = document.getElementById('news-chevron');
    const toggle = content.closest('.news-panel').querySelector('.news-toggle');

    const isCollapsed = content.classList.contains('collapsed');
    if (isCollapsed) {
        content.classList.remove('collapsed');
        chevron.classList.remove('collapsed');
        toggle.setAttribute('aria-expanded', 'true');
    } else {
        content.classList.add('collapsed');
        chevron.classList.add('collapsed');
        toggle.setAttribute('aria-expanded', 'false');
    }
}

// ---------------------------------------------------------------------------
// Status & Auto-refresh
// ---------------------------------------------------------------------------

async function updateStatus() {
    const data = await apiFetch('/api/status');
    if (!data) return;

    document.getElementById('last-crawl-time').textContent =
        data.last_crawl_time ? timeAgo(data.last_crawl_time) : 'Never';

    const flightBadge = document.getElementById('flight-count-badge');
    flightBadge.textContent = (data.total_flights || 0) + ' flights';

    const newBadge = document.getElementById('new-flight-count-badge');
    newBadge.textContent = (data.new_flights || 0) + ' new';
}

function startCountdown() {
    refreshCountdown = 60;
    updateCountdownDisplay();

    if (countdownInterval) clearInterval(countdownInterval);

    countdownInterval = setInterval(function () {
        refreshCountdown--;
        if (refreshCountdown <= 0) {
            refreshCountdown = 60;
        }
        updateCountdownDisplay();
    }, 1000);
}

function updateCountdownDisplay() {
    document.getElementById('next-refresh-countdown').textContent = refreshCountdown + 's';
}

async function refreshNow() {
    const btn = document.getElementById('refresh-btn');
    const icon = document.getElementById('refresh-icon');

    btn.disabled = true;
    icon.classList.add('spinning');

    await apiFetch('/api/refresh', { method: 'POST' });

    // Reload all data
    await Promise.all([loadFlights(), updateStatus(), loadAlerts(), loadNews()]);
    // Re-populate alert dropdown after flights are refreshed
    loadDestinations();

    // Reset countdown
    refreshCountdown = 60;
    updateCountdownDisplay();

    icon.classList.remove('spinning');
    btn.disabled = false;
}

function startAutoRefresh() {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);

    autoRefreshInterval = setInterval(function () {
        refreshCountdown = 60;
        loadFlights();
        updateStatus();
    }, 60000);
}

// ---------------------------------------------------------------------------
// Email Settings
// ---------------------------------------------------------------------------

function toggleEmailSettings() {
    const panel = document.getElementById('email-settings');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

async function loadEmailStatus() {
    const data = await apiFetch('/api/email-settings');
    const banner = document.getElementById('email-banner');
    const icon = document.getElementById('email-banner-icon');
    const text = document.getElementById('email-banner-text');
    const btn = document.getElementById('email-toggle-btn');

    banner.style.display = 'flex';

    if (data && data.configured) {
        banner.className = 'email-banner configured';
        icon.textContent = '\u2705';
        text.textContent = 'Email alerts active';
        btn.textContent = 'Change';
    } else {
        banner.className = 'email-banner not-configured';
        icon.textContent = '\u26A0\uFE0F';
        text.textContent = 'Email not set up';
        btn.textContent = 'Configure';
        // Auto-open settings if not configured
        document.getElementById('email-settings').style.display = 'block';
    }
}

async function saveEmailSettings() {
    const username = document.getElementById('smtp-username').value.trim();
    const password = document.getElementById('smtp-password').value.trim();
    const statusEl = document.getElementById('email-save-status');

    if (!username || !password) {
        statusEl.textContent = 'Both fields are required.';
        statusEl.style.color = 'var(--red)';
        return;
    }

    const result = await apiFetch('/api/email-settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ smtp_username: username, smtp_password: password })
    });

    if (result && result.configured) {
        statusEl.textContent = 'Email settings saved!';
        statusEl.style.color = 'var(--green)';
        loadEmailStatus();
        document.getElementById('smtp-password').value = '';
    } else {
        statusEl.textContent = 'Failed to save settings.';
        statusEl.style.color = 'var(--red)';
    }
}

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', function () {
    // Init multi-select
    initOriginMultiSelect();

    // Load all data - flights first, then destinations (which needs flights for active origins)
    loadFlights().then(function () {
        loadDestinations();
    });
    loadAlerts();
    loadNews();
    updateStatus();
    loadEmailStatus();

    // Set up sort listeners
    document.querySelector('.flight-table thead').addEventListener('click', handleSort);

    // Mark default sort column
    const defaultTh = document.querySelector('th[data-sort="flight_date"]');
    if (defaultTh) defaultTh.classList.add('sort-asc');

    // Start auto-refresh
    startCountdown();
    startAutoRefresh();
});
