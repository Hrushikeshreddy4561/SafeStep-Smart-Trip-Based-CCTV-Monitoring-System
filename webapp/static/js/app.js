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

function getCameraIds() {
    if (Array.isArray(window.CAMERA_IDS) && window.CAMERA_IDS.length > 0) {
        return window.CAMERA_IDS;
    }
    return [0];
}

function cameraFeedUrl(cameraId) {
    return '/video_feed/' + cameraId + '?' + Date.now();
}

function setCameraFeedLive(cameraId) {
    const feed = document.getElementById('camera-feed-' + cameraId);
    const offline = document.getElementById('camera-offline-' + cameraId);
    if (feed) {
        feed.style.display = 'block';
        feed.src = cameraFeedUrl(cameraId);
    }
    if (offline) offline.style.display = 'none';
}

function setCameraFeedOffline(cameraId) {
    const feed = document.getElementById('camera-feed-' + cameraId);
    const offline = document.getElementById('camera-offline-' + cameraId);
    if (feed) {
        feed.src = '';
        feed.style.display = 'none';
    }
    if (offline) offline.style.display = 'flex';
}

async function startCamera() {
    try {
        const resp = await fetch('/api/camera/start', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            getCameraIds().forEach((cameraId) => {
                setTimeout(() => setCameraFeedLive(cameraId), 300);
                updateCameraStatus('standby', cameraId);
            });
        }
    } catch (e) {
        console.error('Failed to start camera:', e);
    }
}

async function stopCamera() {
    try {
        await fetch('/api/camera/stop', { method: 'POST' });
        getCameraIds().forEach((cameraId) => {
            setCameraFeedOffline(cameraId);
            updateCameraStatus('offline', cameraId);
        });
    } catch (e) {
        console.error('Failed to stop camera:', e);
    }
}


// ── Trip Schedule ────────────────────────────────────────────────────────────

function selectedScheduleDays() {
    const root = document.getElementById('schedule-days');
    if (!root) return [];
    return Array.from(root.querySelectorAll('input[type="checkbox"]:checked'))
        .map((el) => el.value);
}

function setScheduleUI(schedule) {
    const startInput = document.getElementById('schedule-start-time');
    const endInput = document.getElementById('schedule-end-time');
    const status = document.getElementById('schedule-status-text');
    const root = document.getElementById('schedule-days');
    if (!startInput || !endInput || !status || !root) return;

    const enabled = !!(schedule && schedule.enabled);
    const days = (enabled && Array.isArray(schedule.days)) ? schedule.days : [];
    root.querySelectorAll('input[type="checkbox"]').forEach((el) => {
        el.checked = days.includes(el.value);
    });

    const activeEl = document.activeElement;
    const editingScheduleTime = (activeEl === startInput || activeEl === endInput);

    if (!editingScheduleTime) {
        if (enabled && schedule.start_time) {
            startInput.value = schedule.start_time;
        }
        if (enabled && schedule.end_time) {
            endInput.value = schedule.end_time;
        }
    }

    if (enabled) {
        const dayText = days.length ? days.join(', ') : 'no days selected';
        const modeText = schedule.active_now ? 'ACTIVE NOW' : 'idle';
        status.textContent =
            'Schedule enabled: ' + schedule.start_time + ' - ' + schedule.end_time +
            ' (' + dayText + ') [' + modeText + ']';
    } else {
        status.textContent = 'Schedule disabled.';
    }
}

async function saveTripSchedule() {
    const startInput = document.getElementById('schedule-start-time');
    const endInput = document.getElementById('schedule-end-time');
    if (!startInput || !endInput) return;

    const start_time = startInput.value;
    const end_time = endInput.value;
    const days = selectedScheduleDays();

    if (!start_time || !end_time) {
        alert('Please choose both start and end time.');
        return;
    }

    try {
        const resp = await fetch('/api/trip/schedule', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ start_time, end_time, days })
        });
        const data = await resp.json();
        if (!data.success) {
            alert(data.message || 'Failed to save schedule.');
            return;
        }
        setScheduleUI(data.schedule);
    } catch (e) {
        console.error('Failed to save schedule:', e);
    }
}

async function disableTripSchedule() {
    try {
        const resp = await fetch('/api/trip/schedule/disable', { method: 'POST' });
        const data = await resp.json();
        if (!data.success) {
            alert(data.message || 'Failed to disable schedule.');
            return;
        }
        setScheduleUI(data.schedule);
    } catch (e) {
        console.error('Failed to disable schedule:', e);
    }
}

async function endTripNowFromSchedule() {
    try {
        const resp = await fetch('/api/trip/stop', { method: 'POST' });
        const data = await resp.json();
        if (!data.success) {
            alert(data.message || 'Failed to stop trip mode.');
        }
    } catch (e) {
        console.error('Failed to stop trip mode:', e);
    }
}


// ── Trip Mode ────────────────────────────────────────────────────────────────

let _tripToggleBusy = false;
let _tripTimerInterval = null;
let _tripElapsedSeconds = 0;

function formatDuration(totalSeconds) {
    const sec = Math.max(0, parseInt(totalSeconds || 0, 10));
    const hours = Math.floor(sec / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    const seconds = sec % 60;
    return `${hours}h ${minutes}m ${seconds}s`;
}

function stopTripDurationTicker() {
    if (_tripTimerInterval) {
        clearInterval(_tripTimerInterval);
        _tripTimerInterval = null;
    }
}

function startTripDurationTicker(initialSeconds) {
    const duration = document.getElementById('trip-duration');
    if (!duration) return;

    stopTripDurationTicker();
    _tripElapsedSeconds = Math.max(0, parseInt(initialSeconds || 0, 10));
    duration.style.display = 'block';
    duration.textContent = formatDuration(_tripElapsedSeconds);

    _tripTimerInterval = setInterval(() => {
        _tripElapsedSeconds += 1;
        duration.textContent = formatDuration(_tripElapsedSeconds);
    }, 1000);
}

function setTripTimerState(isActive, elapsedSeconds) {
    const duration = document.getElementById('trip-duration');
    if (!duration) return;

    if (isActive) {
        startTripDurationTicker(elapsedSeconds || 0);
    } else {
        stopTripDurationTicker();
        _tripElapsedSeconds = 0;
        duration.style.display = 'none';
    }
}

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
                }
                setTripTimerState(true, 0);
                getCameraIds().forEach((cameraId) => {
                    updateCameraStatus('monitoring', cameraId);
                    setTimeout(() => setCameraFeedLive(cameraId), 500);
                });
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
                setTripTimerState(false, 0);
                getCameraIds().forEach((cameraId) => {
                    updateCameraStatus('standby', cameraId);
                    setTimeout(() => setCameraFeedLive(cameraId), 400);
                });
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
            setKnownFacesButtonDisabled(true);
        } else if (data.camera_on) {
            setKnownFacesButtonDisabled(false);
        } else {
            setKnownFacesButtonDisabled(false);
        }

        const cameraList = Array.isArray(data.cameras) ? data.cameras : [];
        if (cameraList.length > 0) {
            cameraList.forEach((camera) => {
                if (camera.trip_active) {
                    updateCameraStatus('monitoring', camera.id);
                } else if (camera.camera_on || camera.preview_active) {
                    updateCameraStatus('standby', camera.id);
                } else {
                    updateCameraStatus('offline', camera.id);
                }

                const feed = document.getElementById('camera-feed-' + camera.id);
                if (!feed) return;

                if ((camera.camera_on || camera.preview_active) && feed.style.display === 'none') {
                    setCameraFeedLive(camera.id);
                } else if (!(camera.camera_on || camera.preview_active) && feed.style.display !== 'none') {
                    setCameraFeedOffline(camera.id);
                }
            });
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
            setTripTimerState(true, data.trip.duration_seconds || 0);
        } else {
            if (statusBadge && !_tripToggleBusy) {
                statusBadge.className = 'trip-status inactive';
                statusBadge.textContent = '⏸ STANDBY';
            }
            setTripTimerState(false, 0);
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

        if (Object.prototype.hasOwnProperty.call(data, 'schedule')) {
            setScheduleUI(data.schedule);
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
                setKnownFacesButtonDisabled(true);
                if (statusBadge && !_tripToggleBusy) {
                    statusBadge.className = 'trip-status active';
                    statusBadge.textContent = '🟢 MONITORING';
                }
                setTripTimerState(true, _tripElapsedSeconds || 0);
            } else {
                setKnownFacesButtonDisabled(false);
                if (statusBadge && !_tripToggleBusy) {
                    statusBadge.className = 'trip-status inactive';
                    statusBadge.textContent = '⏸ STANDBY';
                }
                setTripTimerState(false, 0);
            }

            if (Array.isArray(data.cameras)) {
                data.cameras.forEach((camera) => {
                    if (camera.trip_active) {
                        updateCameraStatus('monitoring', camera.id);
                    } else if (camera.camera_on || camera.preview_active) {
                        updateCameraStatus('standby', camera.id);
                    } else {
                        updateCameraStatus('offline', camera.id);
                    }
                });
            }

            if (Object.prototype.hasOwnProperty.call(data, 'schedule')) {
                setScheduleUI(data.schedule);
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

function updateCameraStatus(status, cameraId) {
    const badge = document.getElementById('camera-status-' + cameraId);
    const text = document.getElementById('status-text-' + cameraId);
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
