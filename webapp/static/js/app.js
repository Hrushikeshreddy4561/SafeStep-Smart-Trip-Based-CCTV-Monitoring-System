/**
 * app.js — Dashboard Logic (v3 — WebSocket + Optimized)
 *
 * Changes from v2:
 *   - Uses WebSocket (Socket.IO) for instant status/alert updates
 *   - Polling reduced to 15s safety net (WebSocket handles real-time)
 *   - Camera feed auto-recovers on page navigation
 *   - Trip toggle state synced from server on every status update
 *   - Toast notifications for alerts pushed from base.html
 */

// ── Camera Controls ──────────────────────────────────────────────────────────

async function startCamera() {
    try {
        const resp = await fetch('/api/camera/start', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            const feed = document.getElementById('camera-feed');
            const offline = document.getElementById('camera-offline');
            if (feed) {
                feed.style.display = 'block';
                setTimeout(() => {
                    feed.src = '/video_feed?' + Date.now();
                }, 300);
            }
            if (offline) offline.style.display = 'none';
            updateCameraStatus('standby');
        }
    } catch (e) {
        console.error('Failed to start camera:', e);
    }
}

async function stopCamera() {
    try {
        await fetch('/api/camera/stop', { method: 'POST' });
        const feed = document.getElementById('camera-feed');
        const offline = document.getElementById('camera-offline');
        if (feed) {
            feed.src = '';
            feed.style.display = 'none';
        }
        if (offline) offline.style.display = 'flex';
        updateCameraStatus('offline');
    } catch (e) {
        console.error('Failed to stop camera:', e);
    }
}


// ── Trip Mode ────────────────────────────────────────────────────────────────

let _tripToggleBusy = false;

async function toggleTripMode(enabled) {
    const toggle = document.getElementById('trip-toggle');
    const statusBadge = document.getElementById('trip-status-badge');
    const duration = document.getElementById('trip-duration');

    if (_tripToggleBusy) {
        toggle.checked = !enabled;
        return;
    }
    _tripToggleBusy = true;

    if (statusBadge) {
        statusBadge.className = 'trip-status inactive';
        statusBadge.textContent = '⏳ Loading...';
    }

    try {
        if (enabled) {
            const resp = await fetch('/api/trip/start', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                if (statusBadge) {
                    statusBadge.className = 'trip-status active';
                    statusBadge.textContent = '🟢 MONITORING';
                }
                setKnownFacesButtonDisabled(true);
                if (duration) {
                    duration.style.display = 'block';
                    duration.textContent = '0h 0m 0s';
                }
                updateCameraStatus('monitoring');
                const feed = document.getElementById('camera-feed');
                if (feed) {
                    setTimeout(() => {
                        feed.src = '/video_feed?' + Date.now();
                        feed.style.display = 'block';
                    }, 500);
                }
            } else {
                toggle.checked = false;
                if (statusBadge) {
                    statusBadge.className = 'trip-status inactive';
                    statusBadge.textContent = '⏸ STANDBY';
                }
                alert(data.message);
            }
        } else {
            const resp = await fetch('/api/trip/stop', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                if (statusBadge) {
                    statusBadge.className = 'trip-status inactive';
                    statusBadge.textContent = '⏸ STANDBY';
                }
                setKnownFacesButtonDisabled(false);
                if (duration) duration.style.display = 'none';
                updateCameraStatus('standby');

                const feed = document.getElementById('camera-feed');
                const offline = document.getElementById('camera-offline');
                if (feed) {
                    setTimeout(() => {
                        feed.src = '/video_feed?' + Date.now();
                        feed.style.display = 'block';
                    }, 400);
                }
                if (offline) offline.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('Trip mode toggle failed:', e);
        toggle.checked = !enabled;
        if (statusBadge) {
            statusBadge.className = 'trip-status inactive';
            statusBadge.textContent = '⏸ STANDBY';
        }
    } finally {
        _tripToggleBusy = false;
    }
}


// ── Status Sync (lightweight — WebSocket handles real-time) ──────────────────

async function pollStatus() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();

        // Update camera status
        if (data.trip_active) {
            updateCameraStatus('monitoring');
            setKnownFacesButtonDisabled(true);
        } else if (data.camera_on) {
            updateCameraStatus('standby');
            setKnownFacesButtonDisabled(false);
        } else {
            updateCameraStatus('offline');
            setKnownFacesButtonDisabled(false);
        }

        // Sync trip toggle with server state (fixes page-switch desync)
        const toggle = document.getElementById('trip-toggle');
        if (toggle && !_tripToggleBusy) {
            toggle.checked = data.trip_active;
        }

        // Update trip duration
        const duration = document.getElementById('trip-duration');
        const statusBadge = document.getElementById('trip-status-badge');
        if (data.trip && data.trip_active) {
            if (statusBadge && !_tripToggleBusy) {
                statusBadge.className = 'trip-status active';
                statusBadge.textContent = '🟢 MONITORING';
            }
            if (duration) {
                duration.style.display = 'block';
                duration.textContent = data.trip.duration;
            }
        } else {
            if (statusBadge && !_tripToggleBusy) {
                statusBadge.className = 'trip-status inactive';
                statusBadge.textContent = '⏸ STANDBY';
            }
            if (duration) duration.style.display = 'none';
        }

        // Update stats
        if (data.stats) {
            const trips = document.getElementById('stat-trips');
            const alerts = document.getElementById('stat-alerts');
            const unrev = document.getElementById('stat-unreviewed');
            if (trips) trips.textContent = data.stats.total_trips;
            if (alerts) alerts.textContent = data.stats.total_alerts;
            if (unrev) unrev.textContent = data.unreviewed_alerts;
        }

        // Update nav badge
        const badge = document.getElementById('nav-alert-count');
        if (badge && data.unreviewed_alerts > 0) {
            badge.textContent = data.unreviewed_alerts;
            badge.style.display = 'inline';
        } else if (badge) {
            badge.style.display = 'none';
        }

        // Auto-show feed if camera is on but feed is hidden (page navigation fix)
        const feed = document.getElementById('camera-feed');
        const offline = document.getElementById('camera-offline');
        if (feed && data.camera_on && feed.style.display === 'none') {
            feed.src = '/video_feed?' + Date.now();
            feed.style.display = 'block';
            if (offline) offline.style.display = 'none';
        }

    } catch (e) {
        // Silent fail on poll
    }
}


// ── WebSocket Event Handlers (Dashboard-specific) ────────────────────────────

function initDashboardSocket() {
    const socket = getSocket();  // defined in base.html
    if (!socket) return;

    // Real-time status updates
    socket.on('status_update', (data) => {
        if (data.trip_active !== undefined) {
            const toggle = document.getElementById('trip-toggle');
            const statusBadge = document.getElementById('trip-status-badge');
            const duration = document.getElementById('trip-duration');

            if (toggle && !_tripToggleBusy) {
                toggle.checked = data.trip_active;
            }

            if (data.trip_active) {
                updateCameraStatus('monitoring');
                setKnownFacesButtonDisabled(true);
                if (statusBadge && !_tripToggleBusy) {
                    statusBadge.className = 'trip-status active';
                    statusBadge.textContent = '🟢 MONITORING';
                }
                if (duration) duration.style.display = 'block';
            } else {
                if (data.camera_on) {
                    updateCameraStatus('standby');
                } else {
                    updateCameraStatus('offline');
                }
                setKnownFacesButtonDisabled(false);
                if (statusBadge && !_tripToggleBusy) {
                    statusBadge.className = 'trip-status inactive';
                    statusBadge.textContent = '⏸ STANDBY';
                }
                if (duration) duration.style.display = 'none';
            }
        }
    });

    // Real-time alert — update stats
    socket.on('new_alert', (data) => {
        const alerts = document.getElementById('stat-alerts');
        if (alerts) {
            alerts.textContent = parseInt(alerts.textContent || '0') + 1;
        }
        const unrev = document.getElementById('stat-unreviewed');
        if (unrev) {
            unrev.textContent = parseInt(unrev.textContent || '0') + 1;
        }
    });
}


// ── Helpers ──────────────────────────────────────────────────────────────────

function updateCameraStatus(status) {
    const badge = document.getElementById('camera-status');
    const text = document.getElementById('status-text');
    if (!badge || !text) return;

    badge.className = 'camera-status-badge';

    switch (status) {
        case 'monitoring':
            badge.classList.add('status-monitoring');
            text.textContent = 'MONITORING';
            break;
        case 'standby':
            badge.classList.add('status-standby');
            text.textContent = 'STANDBY';
            break;
        case 'offline':
            badge.classList.add('status-offline');
            text.textContent = 'OFFLINE';
            break;
        default:
            badge.classList.add('status-standby');
            text.textContent = 'CONNECTING';
    }
}

function setKnownFacesButtonDisabled(disabled) {
    const targets = [
        document.getElementById('btn-known-faces'),
        document.getElementById('nav-known-faces-link')
    ].filter(Boolean);

    targets.forEach((btn) => {
        if (!btn.dataset.href) {
            btn.dataset.href = btn.getAttribute('href') || '';
        }

        if (disabled) {
            btn.setAttribute('href', '#');
            btn.style.pointerEvents = 'none';
            btn.style.opacity = '0.45';
            btn.style.cursor = 'not-allowed';
            btn.title = 'Stop Trip Mode to access Known Faces';
        } else {
            btn.setAttribute('href', btn.dataset.href);
            btn.style.pointerEvents = '';
            btn.style.opacity = '';
            btn.style.cursor = '';
            btn.title = '';
        }
    });
}
