/* Dashboard JS — extracted from index.html */

    function sanitizeDesc(html) {
        // Allow only safe inline tags from AniList descriptions
        var el = document.createElement('div');
        el.innerHTML = html;
        // Strip everything except safe tags
        el.querySelectorAll('*').forEach(function(node) {
            var tag = node.tagName.toLowerCase();
            if (['b', 'i', 'em', 'strong', 'br', 'u', 'a', 'p', 'span'].indexOf(tag) === -1) {
                node.replaceWith(document.createTextNode(node.textContent));
            } else if (tag === 'a') {
                // Only allow href, open in new tab
                var href = node.getAttribute('href') || '';
                Array.from(node.attributes).forEach(function(a) { node.removeAttribute(a.name); });
                if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
                    node.setAttribute('href', href);
                    node.setAttribute('target', '_blank');
                    node.setAttribute('rel', 'noopener');
                } else {
                    node.removeAttribute('href');
                }
            } else {
                // Strip all attributes from safe tags
                Array.from(node.attributes).forEach(function(a) { node.removeAttribute(a.name); });
            }
        });
        // Collapse runs of more than 2 consecutive <br> tags
        return el.innerHTML.replace(/(<br\s*\/?\s*>\s*){3,}/gi, '<br><br>');
    }

    function _statusBadgeHtml(sonarr, hasTvdb) {
        if (sonarr === 'added' || sonarr === 'exists')
            return '<span class="badge badge-added">in sonarr</span>';
        if (hasTvdb)
            return '<span class="badge badge-mapped">ready</span>';
        return '<span class="badge badge-unmapped">unmatched</span>';
    }

    function _coverCellHtml(src) {
        return '<td class="col-cover">' + (src ? '<img src="'+escapeHtml(src)+'" alt="" class="row-cover" loading="lazy">' : '') + '</td>';
    }
    function _titleCellHtml(title, romaji) {
        var html = '<td class="col-title"><div class="title-cell"><span class="title-main"><span class="title-scroll">' + escapeHtml(title) + '</span></span>';
        if (romaji && romaji !== title) html += '<span class="title-sub"><span class="title-scroll">' + escapeHtml(romaji) + '</span></span>';
        return html + '</div></td>';
    }
    function _typeCellHtml(fmt) {
        return '<td class="col-type"><span class="badge badge-' + escapeHtml(fmt.toLowerCase()) + '">' + escapeHtml(fmt) + '</span></td>';
    }
    function _seasonCellHtml(num) {
        return '<td class="col-season">' + (num > 1 ? '<span class="badge badge-sequel">S'+num+'</span>' : '<span class="badge badge-new">New</span>') + '</td>';
    }
    function _epsCellHtml(eps) {
        return '<td class="col-eps"><span class="eps-value">' + (eps || '—') + '</span></td>';
    }
    function _tvdbCellHtml(tvdbId) {
        return '<td class="col-tvdb">' + (tvdbId ? '<span class="tvdb-id">'+tvdbId+'</span>' : '<span class="tvdb-unmatched">—</span>') + '</td>';
    }
    function _sharedCellsHtml(coverSrc, title, romaji, fmt, seasonNum, eps, tvdbId, sonarr) {
        return _coverCellHtml(coverSrc) + _titleCellHtml(title, romaji) + _typeCellHtml(fmt) +
               _seasonCellHtml(seasonNum) + _epsCellHtml(eps) + _tvdbCellHtml(tvdbId) +
               '<td class="col-status">' + _statusBadgeHtml(sonarr, !!tvdbId) + '</td>';
    }

    // Row click — open detail unless clicking interactive elements
    function onRowClick(e, anilistId) {
        // Handle TVDB cell clicks — open TVDB link
        var tvdbCell = e.target.closest('.col-tvdb');
        if (tvdbCell) {
            e.stopPropagation();
            var tvdbId = parseInt(tvdbCell.closest('tr').dataset.tvdb);
            if (tvdbId) openTvdbLink(e, tvdbId);
            return;
        }
        if (e.target.closest('a, button, input, .row-actions, .col-actions, .col-check')) return;
        showDetail(anilistId);
    }

    // Toggle checkbox when clicking anywhere in the check cell
    function toggleRowCheck(e, anilistId) {
        e.stopPropagation();
        const cb = e.currentTarget.querySelector('.row-check');
        if (!cb) return;
        if (e.target !== cb) cb.checked = !cb.checked;
        updateCount();
    }

    // Open TVDB link when clicking anywhere in the TVDB cell
    function openTvdbLink(e, tvdbId) {
        e.stopPropagation();
        if (!tvdbId) return;
        window.open('https://thetvdb.com/dereferrer/series/' + tvdbId, '_blank', 'noopener');
    }

    // Detail modal with prev/next navigation
    let detailCurrentId = null;

    function _getVisibleRowIds() {
        return Array.from(document.querySelectorAll('#anime-table tbody tr'))
            .filter(r => r.style.display !== 'none')
            .map(r => parseInt(r.dataset.anilistId))
            .filter(id => !isNaN(id));
    }

    function showDetail(anilistId) {
        detailCurrentId = anilistId;
        _renderDetail(anilistId);
        document.getElementById('detail-modal').classList.add('active');
    }

    function _renderDetail(anilistId) {
        const a = ANIME_DATA[anilistId];
        if (!a) return;

        detailCurrentId = anilistId;
        const ids = _getVisibleRowIds();
        const idx = ids.indexOf(anilistId);

        // Nav state
        document.getElementById('detail-prev').disabled = idx <= 0;
        document.getElementById('detail-next').disabled = idx >= ids.length - 1 || idx === -1;
        document.getElementById('detail-pos').textContent = idx >= 0 ? `${idx + 1} of ${ids.length}` : '';

        const genres = a.genres ? a.genres.split(',').map(g => '<span class="detail-genre">' + escapeHtml(g.trim()) + '</span>').join('') : '';
        const c = document.getElementById('detail-content');
        c.innerHTML = `
            <div class="detail-header">
                ${a.coverLg ? '<img src="'+escapeHtml(a.coverLg)+'" alt="" class="detail-cover">' : ''}
                <div class="detail-meta">
                    <h2 class="detail-title">${escapeHtml(a.title)}</h2>
                    ${a.titleRomaji && a.titleRomaji !== a.title ? '<div class="detail-alt">'+escapeHtml(a.titleRomaji)+'</div>' : ''}
                    ${a.titleNative ? '<div class="detail-alt detail-native">'+escapeHtml(a.titleNative)+'</div>' : ''}
                    <div class="detail-tags">
                        <span class="badge badge-${escapeHtml(a.format.toLowerCase())}">${escapeHtml(a.format)}</span>
                        ${a.episodes ? '<span class="detail-info">'+a.episodes+' episodes</span>' : ''}
                        ${a.score ? '<span class="detail-info">&#9733; '+a.score+'%</span>' : ''}
                    </div>
                    ${genres ? '<div class="detail-genres">'+genres+'</div>' : ''}
                    ${a.tvdbId ? '<div class="detail-tvdb"><span class="detail-info">TVDB: <a href="https://thetvdb.com/dereferrer/series/'+a.tvdbId+'" target="_blank" rel="noopener">'+a.tvdbId+'</a>'+(a.tvdbTitle?' — '+escapeHtml(a.tvdbTitle):'')+'</span></div>' : ''}
                </div>
            </div>
            ${a.description ? '<div class="detail-desc"><div class="detail-desc-scroll"><div class="detail-desc-inner">'+sanitizeDesc(a.description)+'</div></div></div>' : ''}`;

        const link = document.getElementById('detail-anilist-link');
        if (a.anilistUrl) { link.href = a.anilistUrl; link.style.display = ''; }
        else { link.style.display = 'none'; }
    }

    function detailNav(dir) {
        const ids = _getVisibleRowIds();
        const idx = ids.indexOf(detailCurrentId);
        const next = idx + dir;
        if (next < 0 || next >= ids.length) return;
        const content = document.getElementById('detail-content');
        content.classList.add('fading');
        setTimeout(() => {
            _renderDetail(ids[next]);
            content.scrollTop = 0;
            content.classList.remove('fading');
        }, 150);
    }

    function closeDetail() {
        document.getElementById('detail-modal').classList.remove('active');
        detailCurrentId = null;
    }

    async function detailIgnore() {
        if (!detailCurrentId) return;
        const id = detailCurrentId;

        // Capture the current list and position BEFORE removing the row
        const ids = _getVisibleRowIds();
        const idx = ids.indexOf(id);
        // Determine the next item to show (same index = next item, or fall back to prev)
        const nextIds = ids.filter(function(i) { return i !== id; });
        var nextId = null;
        if (nextIds.length > 0) {
            // Same index position in the filtered list, or last item
            nextId = idx < nextIds.length ? nextIds[idx] : nextIds[nextIds.length - 1];
        }

        const d = await apiCall('/api/ignore', { method:'POST', body:JSON.stringify({anilist_ids:[id], ignored:true}) });
        if (d?.status === 'ok') {
            showToast(d.message);
            const row = document.querySelector(`#anime-table tr[data-anilist-id="${id}"]`);
            if (row) {
                // Hide immediately so counts and navigation exclude it
                row.style.display = 'none';
                _moveRowToIgnored(row);
            }
            _updateStats();

            if (nextId) {
                _renderDetail(nextId);
            } else {
                closeDetail();
            }
        } else if (d) showToast(d.message);
    }

    // Keyboard nav for detail modal
    document.addEventListener('keydown', function(e) {
        if (!document.getElementById('detail-modal').classList.contains('active')) return;
        if (e.key === 'ArrowLeft') detailNav(-1);
        else if (e.key === 'ArrowRight') detailNav(1);
        else if (e.key === 'Escape') closeDetail();
    });

    // Selection
    const selectAll = document.getElementById('select-all');
    const btnSync = document.getElementById('btn-sync');
    const btnIgnore = document.getElementById('btn-ignore-selected');

    function updateCount() {
        const checks = document.querySelectorAll('.row-check');
        let n = 0, total = checks.length;
        checks.forEach(function(cb) {
            var row = cb.closest('tr');
            if (cb.checked) { n++; row && row.classList.add('row-selected'); }
            else { row && row.classList.remove('row-selected'); }
        });
        btnIgnore.disabled = n === 0;
        btnSync.disabled = n === 0;
        var sa = document.getElementById('select-all');
        if (sa) {
            sa.checked = n > 0 && n === total;
            sa.indeterminate = n > 0 && n < total;
        }
    }

    function confirmIgnoreSelected() {
        const checks = Array.from(document.querySelectorAll('.row-check:checked'));
        const n = checks.length;
        if (!n) return;
        showConfirmModal(
            'Ignore ' + n + ' series?',
            n === 1
                ? 'This series will be moved to the ignored list. You can restore it later.'
                : 'These ' + n + ' series will be moved to the ignored list. You can restore them later.',
            'Continue',
            function() { ignoreSelected(); closeConfirmModal(); }
        );
    }

    // Use event delegation so dynamically added checkboxes work after tab switch
    document.addEventListener('change', function(e) {
        if (e.target.id === 'select-all') {
            var checked = e.target.checked;
            document.querySelectorAll('#anime-table tbody tr').forEach(function(row) {
                if (row.style.display === 'none') return;
                var cb = row.querySelector('.row-check');
                if (cb) cb.checked = checked;
            });
            updateCount();
        } else if (e.target.classList.contains('row-check')) {
            updateCount();
        }
    });

    // Sorting
    let sortCol = 'title', sortDir = 1;
    document.querySelectorAll('.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            if (sortCol === col) sortDir *= -1; else { sortCol = col; sortDir = 1; }
            document.querySelectorAll('.sortable').forEach(h => h.classList.remove('sort-asc','sort-desc'));
            th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
            const tbody = document.querySelector('#anime-table tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            rows.sort((a,b) => {
                let va = a.dataset[col]||'', vb = b.dataset[col]||'';
                if (['episodes','tvdb'].includes(col)) return ((parseFloat(va)||0)-(parseFloat(vb)||0))*sortDir;
                return va.localeCompare(vb)*sortDir;
            });
            rows.forEach(r => tbody.appendChild(r));
        });
    });

    // --- Filtering (search + stat clicks) ---
    var activeStatFilter = null;

    function rowMatchesFilter(row, filter) {
        if (filter === 'total') return true;
        if (filter === 'mapped') return parseInt(row.dataset.tvdb || '0') > 0;
        if (filter === 'unmapped') return !parseInt(row.dataset.tvdb || '0');
        if (filter === 'added') return row.dataset.sonarr === 'added' || row.dataset.sonarr === 'exists';
        return true;
    }

    function applyFilters() {
        document.querySelectorAll('.row-check:checked').forEach(function(cb) { cb.checked = false; });
        var sa = document.getElementById('select-all');
        if (sa) sa.checked = false;
        updateCount();
        var q = (document.getElementById('table-search') || {}).value || '';
        q = q.toLowerCase().trim();
        var visibleCount = 0;
        document.querySelectorAll('#anime-table tbody tr').forEach(function(row) {
            var title = (row.dataset.title || '').toLowerCase();
            var matchesSearch = !q || title.includes(q);
            var matchesFilter = !activeStatFilter || rowMatchesFilter(row, activeStatFilter);
            var visible = matchesSearch && matchesFilter;
            row.style.display = visible ? '' : 'none';
            if (visible) visibleCount++;
        });
        var wrapper = document.getElementById('table-wrapper');
        if (wrapper) wrapper.classList.toggle('has-visible-rows', visibleCount > 0);
    }

    (function() {
        var searchInput = document.getElementById('table-search');
        if (searchInput) searchInput.addEventListener('input', applyFilters);
    })();

    document.querySelectorAll('.toolbar-stat[data-filter]').forEach(function(stat) {
        stat.addEventListener('click', function() {
            var filter = this.dataset.filter;
            if (activeStatFilter === filter) {
                activeStatFilter = null;
                this.classList.remove('active');
            } else {
                document.querySelectorAll('.toolbar-stat.active').forEach(function(s) { s.classList.remove('active'); });
                activeStatFilter = filter;
                this.classList.add('active');
            }
            applyFilters();
        });
    });

    // TVDB Match Modal
    let modalAnilistId = null;
    let modalSelectedTvdb = null;

    function editTvdb(id, tvdb) {
        modalAnilistId = id;
        modalSelectedTvdb = tvdb || null;
        const a = ANIME_DATA[id];
        const row = document.querySelector(`tr[data-anilist-id="${id}"]`);
        document.getElementById('modal-anime-title').textContent = row ? row.querySelector('.title-main').textContent.trim() : (a ? a.title : '');

        // Show current mapping
        const currentEl = document.getElementById('tvdb-current');
        if (tvdb && a) {
            currentEl.innerHTML = `
                <div class="tvdb-current-mapped">
                    <div class="tvdb-current-label">Current mapping</div>
                    <div class="tvdb-current-id">${tvdb}</div>
                    ${a.tvdbTitle ? `<div class="tvdb-current-title">${escapeHtml(a.tvdbTitle)}</div>` : ''}
                    ${a.mappingSource ? `<span class="tvdb-tag tvdb-tag-source">${escapeHtml(a.mappingSource)}</span>` : ''}
                </div>`;
            currentEl.style.display = '';
        } else {
            currentEl.innerHTML = '<div class="tvdb-current-unmatched">No TVDB mapping</div>';
            currentEl.style.display = '';
        }

        // Pre-fill input with existing ID
        document.getElementById('modal-tvdb-input').value = tvdb || '';
        document.getElementById('modal-verify-preview').style.display = 'none';
        document.getElementById('modal-clear-btn').style.display = tvdb ? '' : 'none';

        document.getElementById('tvdb-modal').classList.add('active');
        document.getElementById('modal-tvdb-input').focus();
    }

    function closeTvdbModal() {
        document.getElementById('tvdb-modal').classList.remove('active');
        modalAnilistId = null;
        modalSelectedTvdb = null;
    }

    async function verifyManualTvdb() {
        const v = document.getElementById('modal-tvdb-input').value.trim();
        if (!v) return;
        const p = document.getElementById('modal-verify-preview'), c = document.getElementById('modal-verify-content');
        c.innerHTML = '<span class="text-muted">Verifying...</span>';
        p.style.display = '';
        const d = await apiCall('/api/tvdb/verify', { method:'POST', body:JSON.stringify({tvdb_id:parseInt(v)}) });
        if (d?.status === 'ok' && d.series) {
            const s = d.series;
            p.className = 'tvdb-verify-preview tvdb-verify-ok';
            c.innerHTML = `<div class="preview-title">${escapeHtml(s.title)}</div><div class="preview-meta">${escapeHtml(s.year||'?')} &middot; ${escapeHtml(s.status||'?')}${s.country?' &middot; '+escapeHtml(s.country):''}</div>`;
            modalSelectedTvdb = parseInt(v);
        } else if (d?.status === 'not_configured') {
            p.className = 'tvdb-verify-preview tvdb-verify-warn';
            c.innerHTML = `<span class="verify-status-msg">${escapeHtml(d.message)}</span>`;
        } else {
            p.className = 'tvdb-verify-preview tvdb-verify-err';
            c.innerHTML = `<span class="verify-status-msg">TVDB ID ${v} not found</span>`;
        }
    }

    async function saveTvdb() {
        if (!modalAnilistId) return;
        const tvdbId = modalSelectedTvdb || document.getElementById('modal-tvdb-input').value.trim();
        if (!tvdbId) { showToast('Select or enter a TVDB ID'); return; }
        setLoading(true);
        const d = await apiCall('/api/tvdb/set', { method:'POST', body:JSON.stringify({anilist_id:modalAnilistId, tvdb_id:parseInt(tvdbId)}) });
        setLoading(false);
        if (d) showToast(d.message);
        closeTvdbModal();
        if (d?.status==='ok') {
            _updateRowTvdb(modalAnilistId, d.tvdb_id, d.tvdb_title, 'manual');
        }
    }

    function clearTvdb() {
        if (!modalAnilistId) return;
        showConfirmModal('Clear TVDB Mapping', 'This will remove the current TVDB mapping for this anime.', 'Clear', async function() {
            closeConfirmModal();
            setLoading(true);
            const id = modalAnilistId;
            const d = await apiCall('/api/tvdb/set', { method:'POST', body:JSON.stringify({anilist_id:id, tvdb_id:null}) });
            setLoading(false);
            if (d) showToast(d.message);
            closeTvdbModal();
            if (d?.status==='ok') {
                _updateRowTvdb(id, null, null, null);
            }
        });
    }

    document.getElementById('modal-tvdb-input').addEventListener('keydown', e => { if (e.key==='Enter') { e.preventDefault(); verifyManualTvdb(); } });

    // --- Inline row update helpers ---
    function _updateRowTvdb(anilistId, tvdbId, tvdbTitle, source) {
        const row = document.querySelector(`#anime-table tr[data-anilist-id="${anilistId}"]`);
        if (!row) return;
        row.dataset.tvdb = tvdbId || 0;

        if (ANIME_DATA[anilistId]) {
            ANIME_DATA[anilistId].tvdbId = tvdbId;
            ANIME_DATA[anilistId].tvdbTitle = tvdbTitle || '';
            ANIME_DATA[anilistId].mappingSource = source || '';
        }

        const tvdbCell = row.querySelector('.col-tvdb');
        const actionsCell = row.querySelector('.col-actions');

        if (tvdbId) {
            row.classList.remove('row-unmapped');
            tvdbCell.setAttribute('onclick', `openTvdbLink(event, ${tvdbId})`);
            tvdbCell.innerHTML = `<div class="tvdb-cell"><span class="tvdb-id">${tvdbId}</span><span class="tvdb-tag tvdb-tag-source">${escapeHtml(source||'')}</span></div>`;
            actionsCell.innerHTML = `<div class="row-actions"><button class="action-btn" onclick="editTvdb(${anilistId}, ${tvdbId})" title="Edit TVDB ID"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg></button><button class="action-btn action-btn-danger" onclick="ignoreSingle(${anilistId})" title="Ignore"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></button></div>`;
        } else {
            row.classList.add('row-unmapped');
            tvdbCell.setAttribute('onclick', 'event.stopPropagation()');
            tvdbCell.innerHTML = '<span class="tvdb-unmatched">unmatched</span>';
            actionsCell.innerHTML = `<div class="row-actions"><button class="action-btn" onclick="editTvdb(${anilistId}, null)" title="Edit TVDB ID"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg></button><button class="action-btn action-btn-danger" onclick="ignoreSingle(${anilistId})" title="Ignore"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></button></div>`;
        }
        row.style.transition = 'background 0.5s';
        row.style.background = 'var(--color-primary-light)';
        setTimeout(() => { row.style.background = ''; }, 800);
        updateCount();
    }

    function _updateRowSonarrStatus(anilistId, status) {
        const row = document.querySelector(`#anime-table tr[data-anilist-id="${anilistId}"]`);
        if (!row) return;
        row.dataset.sonarr = status;
        if (ANIME_DATA[anilistId]) ANIME_DATA[anilistId].sonarrStatus = status;
        const statusCell = row.querySelector('.col-status');
        if (statusCell) {
            const hasTvdb = row.dataset.tvdb && row.dataset.tvdb !== '0';
            statusCell.innerHTML = _statusBadgeHtml(status, hasTvdb);
        }
    }

    // Scan menu
    function toggleScanMenu(e) {
        e.stopPropagation();
        var menu = document.getElementById('scan-menu');
        menu.classList.toggle('open');
        // Close on outside click
        if (menu.classList.contains('open')) {
            setTimeout(function() {
                document.addEventListener('click', closeScanMenu, { once: true });
            }, 0);
        }
    }
    function closeScanMenu() {
        document.getElementById('scan-menu').classList.remove('open');
    }

    async function doFreshRefresh() {
        closeScanMenu();
        await apiCall('/api/cache/clear', { method: 'POST' });
        doRefresh(true);
    }

    // Refresh with progress modal
    async function setCurrentSeason(btn) {
        if (btn) btn.disabled = true;
        try {
            const d = await apiCall('/api/season/set-current', {
                method: 'POST',
                body: JSON.stringify({ season: SEASON, year: YEAR }),
            });
            if (d && d.status === 'ok') {
                // Reload at this season so the server re-renders tab badges
                // (now/next/past) and adds the new "next" season tab.
                window.location.href = '/?season=' + encodeURIComponent(SEASON) + '&year=' + encodeURIComponent(YEAR);
                return;
            }
            showToast((d && d.message) || 'Could not set current season');
            if (btn) btn.disabled = false;
        } catch (e) {
            showToast('Error: ' + e.message);
            if (btn) btn.disabled = false;
        }
    }

    function toggleYearMenu(e, year) {
        if (e) e.stopPropagation();
        var menu = document.getElementById('season-menu-' + year);
        if (!menu) return;
        var willOpen = !menu.classList.contains('open');
        closeAllYearMenus();
        if (willOpen) {
            var btn = menu.parentElement.querySelector('.year-tab');
            var r = btn.getBoundingClientRect();
            // Fixed-positioned so it floats above the year-tabs scroll clip.
            menu.style.top = (r.bottom + 4) + 'px';
            menu.classList.add('open');
            var left = r.left;
            var mw = menu.offsetWidth;
            if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - 8 - mw);
            menu.style.left = left + 'px';
            if (btn) btn.setAttribute('aria-expanded', 'true');
        }
    }

    function closeAllYearMenus() {
        document.querySelectorAll('.season-menu.open').forEach(function(m) { m.classList.remove('open'); });
        document.querySelectorAll('.year-tab[aria-expanded="true"]').forEach(function(b) { b.setAttribute('aria-expanded', 'false'); });
    }

    // Fixed dropdowns would drift on scroll/resize — just close them.
    window.addEventListener('scroll', closeAllYearMenus, true);
    window.addEventListener('resize', closeAllYearMenus);

    // Re-sync year tabs + dropdowns after an AJAX season switch. The current
    // (green dot) markers are server-rendered and never move; only the blue
    // 'active' highlight and the inline season label on the active year change.
    function updateSeasonNav(season, year) {
        year = parseInt(year, 10);
        var label = season.charAt(0) + season.slice(1).toLowerCase();
        document.querySelectorAll('.year-tab-wrap').forEach(function(wrap) {
            var isActiveYear = parseInt(wrap.dataset.year, 10) === year;
            var btn = wrap.querySelector('.year-tab');
            if (btn) btn.classList.toggle('active', isActiveYear);
            var lbl = wrap.querySelector('.year-tab-season');
            if (lbl) lbl.textContent = isActiveYear ? label : '';
        });
        document.querySelectorAll('.season-menu-item').forEach(function(it) {
            it.classList.toggle('active', parseInt(it.dataset.year, 10) === year && it.dataset.season === season);
        });
        var setBtn = document.getElementById('btn-set-current');
        if (setBtn) {
            var isCur = (season === CUR_SEASON && year === CUR_YEAR);
            setBtn.disabled = isCur;
            setBtn.title = isCur ? 'This is already the current season'
                                 : "Mark this season as the current ('now') season";
        }
        closeAllYearMenus();
    }

    function doRefresh(fresh) {
        const modal = document.getElementById('refresh-modal');
        const bar = document.getElementById('refresh-bar');
        const title = document.getElementById('refresh-title');
        const subtitle = document.getElementById('refresh-subtitle');
        const log = document.getElementById('refresh-log');
        const iconWrap = document.getElementById('refresh-icon-wrap');
        const rsFound = document.getElementById('rs-found');
        const rsMapped = document.getElementById('rs-mapped');
        const rsUnmapped = document.getElementById('rs-unmapped');

        var cancelBtn = document.getElementById('refresh-cancel-btn');

        // Reset state
        bar.style.width = '0%';
        title.textContent = 'Scanning...';
        subtitle.textContent = 'Connecting to AniList';
        log.innerHTML = '';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.className = 'btn';
        cancelBtn.onclick = cancelRefresh;
        iconWrap.classList.remove('done', 'error');
        iconWrap.classList.add('spinning');
        rsFound.textContent = '-';
        rsMapped.textContent = '-';
        rsUnmapped.textContent = '-';
        modal.classList.add('active');

        let mapped = 0, unmapped = 0, total = 0;

        _refreshES = new EventSource(`/api/refresh/stream?season=${SEASON}&year=${YEAR}${fresh ? '&fresh=1' : ''}`);
        const es = _refreshES;

        es.onmessage = function(e) {
            const d = JSON.parse(e.data);

            if (d.progress !== undefined) {
                bar.style.width = d.progress + '%';
            }

            if (d.step === 'fetch') {
                subtitle.textContent = d.detail;
                if (d.count) {
                    total = d.count;
                    rsFound.textContent = total;
                }
            } else if (d.step === 'mapping') {
                subtitle.textContent = d.detail;
            } else if (d.step === 'match') {
                const name = d.detail;
                const idx = d.index || 0;
                subtitle.textContent = `Matching ${idx}/${d.total || total}: ${name}`;
                if (d.searching) {
                    // Pre-resolve event — just update subtitle, don't log yet
                } else if (d.matched) {
                    mapped++;
                    addLog(name, 'matched');
                    rsMapped.textContent = mapped;
                    rsUnmapped.textContent = unmapped;
                } else {
                    unmapped++;
                    addLog(name, 'unmapped');
                    rsMapped.textContent = mapped;
                    rsUnmapped.textContent = unmapped;
                }
            } else if (d.step === 'done') {
                title.textContent = 'Scan Complete';
                subtitle.textContent = d.detail;
                bar.style.width = '100%';
                iconWrap.classList.remove('spinning');
                iconWrap.classList.add('done');
                rsMapped.textContent = d.mapped || mapped;
                rsUnmapped.textContent = d.unmapped || unmapped;
                rsFound.textContent = d.total || total;
                _switchToClose();
                es.close();
                _refreshES = null;
            } else if (d.step === 'error') {
                title.textContent = 'Scan Failed';
                subtitle.textContent = d.detail;
                iconWrap.classList.remove('spinning');
                iconWrap.classList.add('error');
                _switchToClose();
                es.close();
                _refreshES = null;
            }
        };

        es.onerror = function() {
            title.textContent = 'Connection Lost';
            subtitle.textContent = 'The scan stream was interrupted';
            iconWrap.classList.remove('spinning');
            iconWrap.classList.add('error');
            _switchToClose();
            es.close();
            _refreshES = null;
        };

        function _switchToClose() {
            cancelBtn.textContent = 'Close';
            cancelBtn.className = 'btn btn-primary';
            cancelBtn.onclick = closeRefreshModal;
        }

        function addLog(name, status) {
            const el = document.createElement('div');
            el.className = 'refresh-log-item ' + status;
            if (status === 'matched') {
                el.innerHTML = '<span class="log-icon">&#10003;</span><span class="log-name">' + escapeHtml(name) + '</span>';
            } else {
                el.innerHTML = '<span class="log-icon miss">&#10005;</span><span class="log-name">' + escapeHtml(name) + '</span>';
            }
            log.appendChild(el);
            log.scrollTop = log.scrollHeight;
        }
    }

    var _refreshES = null;

    function cancelRefresh() {
        if (_refreshES) {
            _refreshES.close();
            _refreshES = null;
        }
        document.getElementById('refresh-modal').classList.remove('active');
        location.reload();
    }

    function closeRefreshModal() {
        document.getElementById('refresh-modal').classList.remove('active');
        location.reload();
    }

    async function rescanSingle(id) {
        setLoading(true);
        try {
            const d = await apiCall('/api/rescan', { method:'POST', body:JSON.stringify({anilist_ids:[id]}) });
            if (d) showToast(d.message);
            if (d?.status === 'ok' && d.updated) {
                for (const [aid, info] of Object.entries(d.updated)) {
                    _updateRowTvdb(parseInt(aid), info.tvdb_id, info.tvdb_title, info.mapping_source);
                }
            }
        } catch(e) { showToast('Error: '+e.message); }
        finally { setLoading(false); }
    }

    function _updateStats() {
        var rows = document.querySelectorAll('#anime-table tbody tr');
        var total = rows.length, mapped = 0, unmapped = 0, added = 0;
        rows.forEach(function(r) {
            var tvdb = r.dataset.tvdb;
            var sonarr = r.dataset.sonarr;
            if (tvdb && tvdb !== '0') mapped++;
            else unmapped++;
            if (sonarr === 'added' || sonarr === 'exists') added++;
        });
        document.getElementById('stat-total').textContent = total;
        document.getElementById('stat-mapped').textContent = mapped;
        document.getElementById('stat-unmapped').textContent = unmapped;
        document.getElementById('stat-added').textContent = added;
    }

    var _syncCancelled = false;

    async function syncSelected() {
        // Collect all checked IDs, separate matched from unmatched
        const allChecked = Array.from(document.querySelectorAll('.row-check:checked')).map(cb => parseInt(cb.value));
        if (!allChecked.length) { showToast('No series selected'); return; }
        const ids = [];
        const unmatchedNames = [];
        allChecked.forEach(id => {
            const row = document.querySelector(`#anime-table tr[data-anilist-id="${id}"]`);
            if (row && row.dataset.tvdb && row.dataset.tvdb !== '0') {
                ids.push(id);
            } else if (row) {
                unmatchedNames.push(row.querySelector('.title-main')?.textContent?.trim() || 'Unknown');
            }
        });

        _syncCancelled = false;

        // Open sync modal
        const modal = document.getElementById('sync-modal');
        const bar = document.getElementById('sync-bar');
        const title = document.getElementById('sync-title');
        const subtitle = document.getElementById('sync-subtitle');
        const log = document.getElementById('sync-log');
        const iconWrap = document.getElementById('sync-icon-wrap');
        const addedEl = document.getElementById('sync-added');
        const skippedEl = document.getElementById('sync-skipped');
        const errorsEl = document.getElementById('sync-errors');
        const cancelBtn = document.getElementById('sync-cancel-btn');

        bar.style.width = '0%';
        title.textContent = 'Adding to Sonarr...';
        subtitle.textContent = `Processing ${ids.length} series`;
        log.innerHTML = '';
        iconWrap.className = 'refresh-icon-wrap spinning';
        addedEl.textContent = '0';
        skippedEl.textContent = '0';
        errorsEl.textContent = '0';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.className = 'btn';
        cancelBtn.onclick = function() { _syncCancelled = true; closeSyncModal(); };
        modal.classList.add('active');

        let added = 0, skipped = unmatchedNames.length, errors = 0;
        skippedEl.textContent = skipped;

        function addSyncLog(name, status, tag) {
            const el = document.createElement('div');
            el.className = 'refresh-log-item ' + status;
            if (status === 'added') {
                el.innerHTML = '<span class="log-icon">&#10003;</span><span class="log-name">' + escapeHtml(name) + '</span><span class="log-tag">added</span>';
            } else if (status === 'skipped') {
                el.innerHTML = '<span class="log-icon skip">&#8226;</span><span class="log-name">' + escapeHtml(name) + '</span><span class="log-tag">' + escapeHtml(tag || 'skipped') + '</span>';
            } else {
                el.innerHTML = '<span class="log-icon miss">&#10005;</span><span class="log-name">' + escapeHtml(name) + '</span><span class="log-tag">' + escapeHtml(tag || 'error') + '</span>';
            }
            log.appendChild(el);
            log.scrollTop = log.scrollHeight;
        }

        // Log unmatched items as skipped
        unmatchedNames.forEach(name => addSyncLog(name, 'skipped', 'no TVDB'));

        try {
            const d = ids.length ? await apiCall('/api/sync', { method:'POST', body:JSON.stringify({anilist_ids:ids}) }) : {details:[], statuses:{}};

            if (d?.details) {
                for (let i = 0; i < d.details.length; i++) {
                    if (_syncCancelled) break;
                    const item = d.details[i];
                    const pct = Math.round(((i + 1) / d.details.length) * 100);
                    bar.style.width = pct + '%';
                    subtitle.textContent = `${i + 1}/${d.details.length}: ${item.title}`;

                    if (item.status === 'added') {
                        added++;
                        addSyncLog(item.title, 'added');
                    } else if (item.status === 'exists') {
                        skipped++;
                        addSyncLog(item.title, 'skipped', 'in sonarr');
                    } else {
                        errors++;
                        addSyncLog(item.title, 'error', item.status);
                    }
                    addedEl.textContent = added;
                    skippedEl.textContent = skipped;
                    errorsEl.textContent = errors;
                }
            }

            if (d?.statuses) {
                for (const [aid, status] of Object.entries(d.statuses)) {
                    _updateRowSonarrStatus(parseInt(aid), status);
                }
            }

            bar.style.width = '100%';
            title.textContent = 'Sync Complete';
            subtitle.textContent = d?.message || `${added} added, ${skipped} skipped, ${errors} errors`;
            iconWrap.classList.remove('spinning');
            iconWrap.classList.add('done');

            document.querySelectorAll('.row-check:checked').forEach(cb => { cb.checked = false; });
            if (selectAll) selectAll.checked = false;
            updateCount();
            _updateStats();
        } catch(e) {
            title.textContent = 'Sync Failed';
            subtitle.textContent = e.message;
            iconWrap.classList.remove('spinning');
            iconWrap.classList.add('error');
        }
        cancelBtn.textContent = 'Close';
        cancelBtn.className = 'btn btn-primary';
        cancelBtn.onclick = closeSyncModal;
    }

    function closeSyncModal() {
        document.getElementById('sync-modal').classList.remove('active');
    }

    // Ignore / Restore — inline DOM transitions, no page reload
    function _moveRowToIgnored(row) {
        const id = row.dataset.anilistId;
        const a = ANIME_DATA[id] || {};
        const title = a.displayTitle || stripSeasonSuffix(a.title || (row.querySelector('.title-main') ? row.querySelector('.title-main').textContent.trim() : ''));
        const romaji = a.displayRomaji || (a.titleRomaji ? stripSeasonSuffix(a.titleRomaji) : '');
        const cover = row.querySelector('.row-cover');
        const coverSrc = cover ? cover.src : '';
        const fmt = a.format || (row.querySelector('.col-type .badge') ? row.querySelector('.col-type .badge').textContent.trim() : '');
        const seasonNum = a.seasonNumber || 1;
        const eps = a.episodes || '—';
        const tvdbId = a.tvdbId || '';
        const sonarr = a.sonarrStatus || row.dataset.sonarr || '';

        // Animate out
        row.classList.add('row-fading');
        setTimeout(() => {
            row.remove();
            const tr = document.createElement('tr');
            tr.dataset.anilistId = id;
            tr.className = 'row-entering';
            tr.innerHTML = _sharedCellsHtml(coverSrc, title, romaji, fmt, seasonNum, eps, tvdbId, sonarr)
                + '<td class="col-actions"><button class="action-btn" onclick="restoreSingle('+id+')" title="Restore"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg></button></td>';
            document.getElementById('ignored-tbody').appendChild(tr);
            document.getElementById('ignored-section').style.display = '';
            _updateIgnoredCount();
            updateCount();
        }, 300);
    }

    function _moveRowFromIgnored(ignoredRow) {
        const id = ignoredRow.dataset.anilistId;
        const a = ANIME_DATA[id];
        ignoredRow.classList.add('row-fading');
        setTimeout(() => {
            ignoredRow.remove();
            _updateIgnoredCount();
            if (!a) return;

            const hasTvdb = !!a.tvdbId;
            const title = a.displayTitle || stripSeasonSuffix(a.title);
            const romaji = a.displayRomaji || (a.titleRomaji ? stripSeasonSuffix(a.titleRomaji) : '');
            const fmt = a.format;
            const sonarr = a.sonarrStatus;
            const coverLg = a.coverLg || '';
            const seasonNum = a.seasonNumber || 1;
            const actionsHtml = '<div class="row-actions"><button class="action-btn" onclick="editTvdb('+id+', '+(a.tvdbId || 'null')+')" title="Edit TVDB ID"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg></button><button class="action-btn action-btn-danger" onclick="ignoreSingle('+id+')" title="Ignore"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></button></div>';

            const tr = document.createElement('tr');
            tr.dataset.anilistId = id;
            tr.dataset.title = title.toLowerCase();
            tr.dataset.format = fmt;
            tr.dataset.episodes = a.episodes || 0;
            tr.dataset.tvdb = a.tvdbId || 0;
            tr.dataset.sonarr = sonarr;
            tr.dataset.season = seasonNum;
            if (!hasTvdb) tr.className = 'row-unmapped';
            tr.classList.add('row-entering', 'clickable-row');
            tr.onclick = function(e) { onRowClick(e, parseInt(id)); };
            tr.innerHTML = '<td class="col-check" onclick="toggleRowCheck(event, '+id+')"><input type="checkbox" class="row-check" value="'+id+'"></td>'
                + _sharedCellsHtml(coverLg, title, romaji, fmt, seasonNum, a.episodes || '—', a.tvdbId, sonarr)
                + '<td class="col-actions">' + actionsHtml + '</td>';

            // Re-attach checkbox listener
            const cb = tr.querySelector('.row-check');
            if (cb) cb.addEventListener('change', updateCount);

            // Insert at correct sorted position (by title, ascending)
            const tbody = document.querySelector('#anime-table tbody');
            const existing = Array.from(tbody.querySelectorAll('tr'));
            const titleLower = title.toLowerCase();
            let inserted = false;
            for (const r of existing) {
                if ((r.dataset.title || '') > titleLower) {
                    tbody.insertBefore(tr, r);
                    inserted = true;
                    break;
                }
            }
            if (!inserted) tbody.appendChild(tr);
            updateCount();
        }, 300);
    }

    function _updateIgnoredCount() {
        const n = document.querySelectorAll('#ignored-tbody tr').length;
        document.getElementById('ignored-count-label').textContent = n;
        document.getElementById('ignored-section').style.display = n > 0 ? '' : 'none';
    }

    async function ignoreSingle(id) {
        const row = document.querySelector(`#anime-table tr[data-anilist-id="${id}"]`);
        if (!row) return;
        const d = await apiCall('/api/ignore', { method:'POST', body:JSON.stringify({anilist_ids:[id], ignored:true}) });
        if (d?.status === 'ok') {
            showToast(d.message);
            _moveRowToIgnored(row);
        } else if (d) showToast(d.message);
    }

    async function ignoreSelected() {
        const checks = Array.from(document.querySelectorAll('.row-check:checked'));
        const ids = checks.map(cb => parseInt(cb.value));
        if (!ids.length) return;
        const d = await apiCall('/api/ignore', { method:'POST', body:JSON.stringify({anilist_ids:ids, ignored:true}) });
        if (d?.status === 'ok') {
            showToast(d.message);
            ids.forEach(id => {
                const row = document.querySelector(`#anime-table tr[data-anilist-id="${id}"]`);
                if (row) _moveRowToIgnored(row);
            });
        } else if (d) showToast(d.message);
    }

    async function restoreSingle(id) {
        const row = document.querySelector(`#ignored-tbody tr[data-anilist-id="${id}"]`);
        if (!row) return;
        const d = await apiCall('/api/ignore', { method:'POST', body:JSON.stringify({anilist_ids:[id], ignored:false}) });
        if (d?.status === 'ok') {
            showToast(d.message);
            _moveRowFromIgnored(row);
        } else if (d) showToast(d.message);
    }

    // ── Sticky action bar / table merge on scroll ──
    var resetStickyMerge = (function() {
        var stickyHeader = document.getElementById('action-bar-sticky');
        var actionBar = document.getElementById('action-bar');
        if (!stickyHeader || !actionBar) return function(){};

        var mergeScrollY = null;
        var merged = false;
        var ticking = false;

        function getElements() {
            var tw = document.getElementById('table-wrapper');
            return tw ? { wrapper: tw, thead: tw.querySelector('thead') } : null;
        }

        function merge(els) {
            merged = true;
            mergeScrollY = window.scrollY;
            stickyHeader.classList.add('stuck');
            stickyHeader.offsetHeight;
            if (els.thead) {
                els.thead.classList.add('stuck');
                els.thead.style.top = stickyHeader.getBoundingClientRect().bottom + 'px';
            }
        }

        function unmerge() {
            merged = false;
            mergeScrollY = null;
            stickyHeader.classList.remove('stuck');
            var els = getElements();
            if (els && els.thead) {
                els.thead.classList.remove('stuck');
                els.thead.style.top = '';
            }
        }

        function check() {
            var els = getElements();
            if (!els) { ticking = false; return; }
            if (!merged) {
                var barRect = actionBar.getBoundingClientRect();
                var tableRect = els.wrapper.getBoundingClientRect();
                if (tableRect.top - barRect.bottom <= 1) {
                    merge(els);
                }
            } else if (window.scrollY < mergeScrollY - 4) {
                unmerge();
            } else if (merged && els.thead) {
                els.thead.style.top = stickyHeader.getBoundingClientRect().bottom + 'px';
            }
            ticking = false;
        }

        requestAnimationFrame(check);

        window.addEventListener('scroll', function() {
            if (!ticking) { requestAnimationFrame(check); ticking = true; }
        }, { passive: true });

        window.addEventListener('resize', function() {
            if (merged) {
                var els = getElements();
                if (els && els.thead) {
                    stickyHeader.offsetHeight;
                    els.thead.style.top = stickyHeader.getBoundingClientRect().bottom + 'px';
                }
            }
        });

        return function() {
            unmerge();
            requestAnimationFrame(check);
        };
    })();

    // Overflow detection for title scroll
    // Recalculates every hover to handle dynamic content and resizes
    (function() {
        var SPEED = 60; // px per second

        document.addEventListener('mouseover', function(e) {
            var td = e.target.closest('td.col-title');
            if (!td) return;

            td.querySelectorAll('.title-scroll').forEach(function(scroll) {
                var parent = scroll.parentElement; // .title-main or .title-sub
                // Compare the natural text width vs the visible container
                var textWidth = scroll.scrollWidth;
                var containerWidth = parent.offsetWidth;
                var overflow = textWidth - containerWidth;

                if (overflow > 2) {
                    // Add a small buffer so the last characters are fully visible
                    scroll.classList.add('is-overflowing');
                    scroll.style.setProperty('--scroll-dist', '-' + (overflow + 8) + 'px');
                    scroll.style.setProperty('--scroll-duration', ((overflow + 8) / SPEED).toFixed(2) + 's');
                } else {
                    scroll.classList.remove('is-overflowing');
                    scroll.style.removeProperty('--scroll-dist');
                    scroll.style.removeProperty('--scroll-duration');
                }
            });
        });
    })();

    // ── Client-side tab switching ──
    (function() {
        var SVG_EDIT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
        var SVG_IGNORE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>';
        var SVG_RESTORE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>';

        function esc(s) {
            if (!s) return '';
            var d = document.createElement('div');
            d.textContent = s;
            return d.innerHTML;
        }

        function _renderRow(a, type) {
            var hasTvdb = !!a.tvdb_id;
            var dt = esc(a.display_title);
            var dr = esc(a.display_romaji);
            var sn = a.season_number || 1;
            var statusBadge = _statusBadgeHtml(a.sonarr_status, hasTvdb);
            var subTitle = (dr && dr !== dt) ? '<span class="title-sub"><span class="title-scroll">' + dr + '</span></span>' : '';

            var sharedCells = '<td class="col-cover">' + (a.cover_url ? '<img src="' + esc(a.cover_url) + '" alt="" class="row-cover" loading="lazy">' : '') + '</td>'
                + '<td class="col-title"><div class="title-cell"><span class="title-main"><span class="title-scroll">' + dt + '</span></span>' + subTitle + '</div></td>'
                + '<td class="col-type"><span class="badge badge-' + (a.format || '').toLowerCase() + '">' + esc(a.format) + '</span></td>'
                + '<td class="col-season">' + (sn > 1 ? '<span class="badge badge-sequel">S' + sn + '</span>' : '<span class="badge badge-new">New</span>') + '</td>'
                + '<td class="col-eps"><span class="eps-value">' + (a.episodes || '\u2014') + '</span></td>';

            if (type === 'ignored') {
                return '<tr data-anilist-id="' + a.anilist_id + '">'
                    + sharedCells
                    + '<td class="col-tvdb">' + (hasTvdb ? '<span class="tvdb-id">' + a.tvdb_id + '</span>' : '<span class="tvdb-unmatched">\u2014</span>') + '</td>'
                    + '<td class="col-status">' + statusBadge + '</td>'
                    + '<td class="col-actions"><button class="action-btn" onclick="restoreSingle(' + a.anilist_id + ')" title="Restore">' + SVG_RESTORE + '</button></td></tr>';
            }

            return '<tr data-anilist-id="' + a.anilist_id + '"'
                + ' data-title="' + esc((a.title_english || a.title_romaji || '').toLowerCase()) + '"'
                + ' data-format="' + esc(a.format) + '"'
                + ' data-episodes="' + (a.episodes || 0) + '"'
                + ' data-tvdb="' + (a.tvdb_id || 0) + '"'
                + ' data-sonarr="' + esc(a.sonarr_status) + '"'
                + ' data-season="' + sn + '"'
                + ' class="clickable-row' + (hasTvdb ? '' : ' row-unmapped') + '"'
                + ' onclick="onRowClick(event,' + a.anilist_id + ')">'
                + '<td class="col-check" onclick="toggleRowCheck(event,' + a.anilist_id + ')"><input type="checkbox" class="row-check" value="' + a.anilist_id + '"></td>'
                + sharedCells
                + '<td class="col-tvdb" onclick="openTvdbLink(event,' + (a.tvdb_id || 'null') + ')">' + (hasTvdb ? '<span class="tvdb-id">' + a.tvdb_id + '</span>' : '<span class="tvdb-unmatched">\u2014</span>') + '</td>'
                + '<td class="col-status">' + statusBadge + '</td>'
                + '<td class="col-actions"><div class="row-actions">'
                + '<button class="action-btn" onclick="editTvdb(' + a.anilist_id + ',' + (a.tvdb_id || 'null') + ')" title="Edit TVDB ID">' + SVG_EDIT + '</button>'
                + '<button class="action-btn action-btn-danger" onclick="ignoreSingle(' + a.anilist_id + ')" title="Ignore">' + SVG_IGNORE + '</button>'
                + '</div></td></tr>';
        }

        function renderAnimeRow(a) { return _renderRow(a, 'active'); }
        function renderIgnoredRow(a) { return _renderRow(a, 'ignored'); }

        function buildAnimeData(list) {
            var data = {};
            list.forEach(function(a) {
                data[a.anilist_id] = {
                    title: a.title_english || a.tvdb_title || a.title_romaji,
                    titleRomaji: a.title_romaji || '',
                    titleNative: a.title_native || '',
                    format: a.format,
                    episodes: a.episodes,
                    description: a.description || '',
                    genres: a.genres || '',
                    score: a.score,
                    anilistUrl: a.anilist_url || '',
                    coverLg: a.cover_url_lg || a.cover_url || '',
                    tvdbId: a.tvdb_id,
                    tvdbTitle: a.tvdb_title || '',
                    mappingSource: a.mapping_source || '',
                    sonarrStatus: a.sonarr_status,
                    seasonNumber: a.season_number || 1
                };
            });
            return data;
        }

        function applySeasonData(data) {
            SEASON = data.season;
            YEAR = data.year;
            ANIME_DATA = buildAnimeData(data.anime);
            if (typeof resetStickyMerge === 'function') resetStickyMerge();

            // Update stats
            document.getElementById('stat-total').textContent = data.stats.total;
            document.getElementById('stat-mapped').textContent = data.stats.mapped;
            document.getElementById('stat-unmapped').textContent = data.stats.unmapped;
            document.getElementById('stat-added').textContent = data.stats.added;

            // Render main table or empty state
            var wrapper = document.getElementById('table-wrapper');
            var emptyState = document.querySelector('.empty-state');
            if (data.anime.length > 0) {
                if (!wrapper) {
                    // Was showing empty state — need to create the table
                    if (emptyState) emptyState.remove();
                    var actionBar = document.getElementById('action-bar-sticky');
                    var html = '<div class="table-wrapper" id="table-wrapper"><table class="data-table" id="anime-table"><thead><tr>'
                        + '<th class="col-check" style="width:3%" onclick="var cb=document.getElementById(\'select-all\');if(event.target!==cb){cb.checked=!cb.checked;cb.dispatchEvent(new Event(\'change\',{bubbles:true}));}"><input type="checkbox" id="select-all" title="Select all mapped"></th>'
                        + '<th class="col-cover" style="width:4%"></th>'
                        + '<th class="sortable col-title" style="width:30%" data-col="title">Title</th>'
                        + '<th class="sortable col-type" style="width:6%" data-col="format">Type</th>'
                        + '<th class="col-season" style="width:6%">Season</th>'
                        + '<th class="sortable col-eps" style="width:4%" data-col="episodes">Eps</th>'
                        + '<th class="sortable col-tvdb" style="width:7%" data-col="tvdb">TVDB ID</th>'
                        + '<th class="col-status" style="width:9%">Status</th>'
                        + '<th class="col-actions" style="width:7%">Actions</th>'
                        + '</tr></thead><tbody></tbody></table></div>';
                    actionBar.insertAdjacentHTML('afterend', html);
                    wrapper = document.getElementById('table-wrapper');
                }
                var tbody = wrapper.querySelector('tbody');
                tbody.innerHTML = data.anime.map(renderAnimeRow).join('');
            } else {
                if (wrapper) wrapper.remove();
                var anchor = document.getElementById('action-bar-sticky');
                var existing = document.querySelector('.empty-state');
                if (!existing) {
                    anchor.insertAdjacentHTML('afterend', '<div class="empty-state">No anime data for <strong>' + esc(data.season) + ' ' + esc(String(data.year)) + '</strong>. Click <strong>Scan</strong> to fetch.</div>');
                } else {
                    existing.innerHTML = 'No anime data for <strong>' + esc(data.season) + ' ' + esc(String(data.year)) + '</strong>. Click <strong>Scan</strong> to fetch.';
                }
            }

            // Render ignored section
            var ignoredSection = document.getElementById('ignored-section');
            if (data.ignored.length > 0) {
                ignoredSection.style.display = '';
                document.getElementById('ignored-count-label').textContent = data.ignored.length;
                document.getElementById('ignored-tbody').innerHTML = data.ignored.map(renderIgnoredRow).join('');
            } else {
                ignoredSection.style.display = 'none';
            }

            // Reset checkboxes and counts
            var selectAll = document.getElementById('select-all');
            if (selectAll) selectAll.checked = false;
            updateCount();

            // Reset search
            var search = document.getElementById('table-search');
            if (search) { search.value = ''; search.dispatchEvent(new Event('input')); }

            // Update page title
            document.title = 'TruAni \u2014 ' + data.season + ' ' + data.year;
        }

        // AJAX-navigate to a season/year, swapping table + nav state without a reload.
        function navigateToSeason(season, year, push) {
            apiCall('/api/season-data?season=' + encodeURIComponent(season) + '&year=' + encodeURIComponent(year))
                .then(function(data) {
                    if (!data) return;
                    applySeasonData(data);
                    updateSeasonNav(data.season, data.year);
                    if (push !== false) {
                        history.pushState({ season: data.season, year: data.year }, '',
                            '/?season=' + data.season + '&year=' + data.year);
                    }
                });
        }

        // Season item (in any year's dropdown) → AJAX-switch to that season/year
        document.getElementById('year-tabs').addEventListener('click', function(e) {
            var item = e.target.closest('.season-menu-item');
            if (!item) return;
            e.preventDefault();
            navigateToSeason(item.dataset.season, parseInt(item.dataset.year, 10));
        });

        // Close any open year dropdown on an outside click
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.year-tab-wrap')) closeAllYearMenus();
        });

        // Back/forward navigation — every history entry is a same-document AJAX state
        window.addEventListener('popstate', function(e) {
            if (e.state && e.state.season && e.state.year) {
                navigateToSeason(e.state.season, e.state.year, false);
            }
        });

        // Store initial state for back navigation
        history.replaceState({ season: SEASON, year: YEAR }, '');
    })();

    // Check for updates (background)
    function dismissUpdateBanner() {
        var banner = document.getElementById('update-banner');
        var version = document.getElementById('update-banner-version').textContent;
        sessionStorage.setItem('update_dismissed', version);
        banner.classList.add('fade-out');
        setTimeout(function() { banner.style.display = 'none'; }, 400);
    }

    apiCall('/api/update/check').then(function(d) {
        if (d && d.update_available) {
            var version = 'v' + d.latest_version;
            if (sessionStorage.getItem('update_dismissed') !== version) {
                document.getElementById('update-banner-version').textContent = version;
                document.getElementById('update-banner').style.display = '';
            }
        }
    });
