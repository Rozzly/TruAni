/**
 * Shared Sonarr configuration helpers used by both settings and setup pages.
 * Accepts element ID maps so the same logic works with different form field IDs.
 */

/**
 * Test Sonarr connection and update status display.
 * @param {Object} ids - Element IDs: { url, key, statusText, statusDot }
 * @param {Object} [opts] - Options: { onSuccess, onFailure, validateFirst }
 */
async function testSonarrConnection(ids, opts) {
    opts = opts || {};
    var url = document.getElementById(ids.url).value.trim();
    var key = document.getElementById(ids.key).value.trim();

    if (opts.validateFirst) {
        if (!url) { showFieldError(ids.url, 'Sonarr URL is required'); return false; }
        if (!key) { showFieldError(ids.key, 'API key is required'); return false; }
    }

    try {
        var resp = await apiCall('/api/settings/test-sonarr', {
            method: 'POST',
            body: JSON.stringify({ sonarr_url: url, sonarr_api_key: key })
        });
        if (resp) {
            document.getElementById(ids.statusText).textContent = resp.message;
            document.getElementById(ids.statusDot).className = 'dot ' + (resp.connected ? 'ok' : 'err');
            if (resp.connected) {
                if (opts.onSuccess) opts.onSuccess(resp);
            } else {
                if (opts.onFailure) opts.onFailure(resp);
                else showToast('Failed: ' + resp.message);
            }
        }
        return resp && resp.connected;
    } catch (e) {
        if (opts.onFailure) opts.onFailure({ message: e.message });
        else showToast('Error: ' + e.message);
        return false;
    }
}

/**
 * Load root folders and quality profiles from Sonarr into select elements.
 * @param {Object} ids - Element IDs: { url, key, rootFolder, qualityProfile }
 */
async function loadSonarrOptions(ids) {
    var url = document.getElementById(ids.url).value.trim();
    var key = document.getElementById(ids.key).value.trim();
    if (!url || !key) return;

    var resp = await apiCall('/api/settings/sonarr-options', {
        method: 'POST',
        body: JSON.stringify({ sonarr_url: url, sonarr_api_key: key })
    });
    if (!resp || resp.status !== 'ok') return;

    _populateSelect(ids.rootFolder, resp.root_folders, function(rf) {
        var gb = Math.round(rf.freeSpace / 1073741824);
        return { value: rf.path, label: rf.path + (gb > 0 ? ' (' + gb + ' GB free)' : '') };
    }, 'No root folders found');

    _populateSelect(ids.qualityProfile, resp.quality_profiles, function(qp) {
        return { value: qp.name, label: qp.name };
    }, 'No profiles found');
}

function _populateSelect(selectId, items, mapper, emptyText) {
    var select = document.getElementById(selectId);
    var current = select.value;
    select.innerHTML = '';
    if (!items || items.length === 0) {
        select.innerHTML = '<option value="" disabled selected>' + escapeHtml(emptyText) + '</option>';
    } else {
        items.forEach(function(item) {
            var mapped = mapper(item);
            var opt = document.createElement('option');
            opt.value = mapped.value;
            opt.textContent = mapped.label;
            if (mapped.value === current) opt.selected = true;
            select.appendChild(opt);
        });
        if (!current && items.length > 0) select.selectedIndex = 0;
    }
}
