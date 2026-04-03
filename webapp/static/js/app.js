/**
 * app.js — Dashboard Logic
 * Handles trip mode toggle, camera controls, and status polling.
 */

// ── Camera Controls ──────────────────────────────────────────────────────────

async function startCamera() {
    try {
        const resp = await fetch('/api/camera/start', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            // Refresh the video feed
            const feed = document.getElementById('camera-feed');
            const offline = document.getElementById('camera-offline');
            if (feed) {
                feed.src = '/video_feed?' + Date.now();
                feed.style.display = 'block';
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
        if (feed) feed.style.display = 'none';
        if (offline) offline.style.display = 'flex';
        updateCameraStatus('offline');
    } catch (e) {
        console.error('Failed to stop camera:', e);
    }
}


// ── Trip Mode ────────────────────────────────────────────────────────────────

async function toggleTripMode(enabled) {
    const toggle = document.getElementById('trip-toggle');
    const statusBadge = document.getElementById('trip-status-badge');
    const duration = document.getElementById('trip-duration');

    try {
        if (enabled) {
            const resp = await fetch('/api/trip/start', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                statusBadge.className = 'trip-status active';
                statusBadge.textContent = '🟢 MONITORING';
                if (duration) {
                    duration.style.display = 'block';
                    duration.textContent = '0h 0m 0s';
                }
                updateCameraStatus('monitoring');
                // Refresh feed to get detection overlays
                const feed = document.getElementById('camera-feed');
                if (feed) {
                    feed.src = '/video_feed?' + Date.now();
                    feed.style.display = 'block';
                }
            } else {
                toggle.checked = false;
                alert(data.message);
            }
        } else {
            const resp = await fetch('/api/trip/stop', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                statusBadge.className = 'trip-status inactive';
                statusBadge.textContent = '⏸️ STANDBY';
                if (duration) duration.style.display = 'none';
                updateCameraStatus('standby');
                // Refresh feed to get preview mode
                const feed = document.getElementById('camera-feed');
                if (feed) {
                    feed.src = '/video_feed?' + Date.now();
                }
            }
        }
    } catch (e) {
        console.error('Trip mode toggle failed:', e);
        toggle.checked = !enabled;
    }
}


// ── Status Polling ───────────────────────────────────────────────────────────

let _tripStartTime = null;

async function pollStatus() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();

        // Update camera status
        if (data.trip_active) {
            updateCameraStatus('monitoring');
        } else if (data.camera_on) {
            updateCameraStatus('standby');
        } else {
            updateCameraStatus('offline');
        }

        // Update trip toggle
        const toggle = document.getElementById('trip-toggle');
        if (toggle) toggle.checked = data.trip_active;

        // Update trip duration
        const duration = document.getElementById('trip-duration');
        const statusBadge = document.getElementById('trip-status-badge');
        if (data.trip && data.trip_active) {
            if (statusBadge) {
                statusBadge.className = 'trip-status active';
                statusBadge.textContent = '🟢 MONITORING';
            }
            if (duration) {
                duration.style.display = 'block';
                duration.textContent = data.trip.duration;
            }
        } else {
            if (statusBadge) {
                statusBadge.className = 'trip-status inactive';
                statusBadge.textContent = '⏸️ STANDBY';
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

    } catch (e) {
        // Silent fail on poll
    }
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
            text.textContent = '● MONITORING';
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
