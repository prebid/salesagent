/* Signals page — source-centric redesign.
 *
 * Server renders the full page (segments / keys / composites). This script
 * adds the interactive layer: filters, selection state, inline rename,
 * overflow menus, adaptive bulk-action bar.
 */
(function () {
  'use strict';

  const root = document.querySelector('.signals-page');
  if (!root) return;

  const scriptRoot = root.dataset.scriptRoot || '';
  const tenantId = root.dataset.tenantId;
  const urls = {
    bulkCreate: `${scriptRoot}/tenant/${tenantId}/signals/bulk-create`,
    bulkUpdate: `${scriptRoot}/tenant/${tenantId}/signals/bulk-update`,
    bulkDelete: `${scriptRoot}/tenant/${tenantId}/signals/bulk-delete`,
    signalDelete: (sid) => `${scriptRoot}/tenant/${tenantId}/signals/${encodeURIComponent(sid)}/delete`,
    signalRename: (sid) => `${scriptRoot}/tenant/${tenantId}/signals/${encodeURIComponent(sid)}/rename`,
    targetingValues: (keyId) => `${scriptRoot}/api/tenant/${tenantId}/targeting/values/${encodeURIComponent(keyId)}`,
  };

  // ---------- Show IDs toggle ----------
  const idsToggle = document.getElementById('show-ids-toggle');
  if (idsToggle) {
    idsToggle.addEventListener('change', () => {
      root.classList.toggle('show-ids', idsToggle.checked);
    });
    root.classList.toggle('show-ids', idsToggle.checked);
  }
  // Apply current show-ids state to all .id-only elements (rendered visible
  // when root has .show-ids, hidden otherwise via CSS rule below).
  injectShowIdsStyles();

  function injectShowIdsStyles() {
    const css = `
      .signals-page .row-main__id { display: none; }
      .signals-page.show-ids .row-main__id { display: inline; }
      .signals-page .col-gam-id { display: none; }
      .signals-page.show-ids .col-gam-id { display: table-cell; }
    `;
    const tag = document.createElement('style');
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  // ---------- Status segmented control + search + tag filter ----------
  const state = {
    status: 'all',     // 'all' | 'mapped' | 'unmapped'
    search: '',
    tag: null,
  };

  document.querySelectorAll('.seg button[data-status]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.status = btn.dataset.status;
      document.querySelectorAll('.seg button[data-status]').forEach((b) =>
        b.classList.toggle('active', b === btn),
      );
      applyFilters();
    });
  });

  const searchInput = document.getElementById('signals-search');
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      state.search = searchInput.value.trim().toLowerCase();
      applyFilters();
    });
  }

  document.querySelectorAll('.tag-bar .pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      state.tag = state.tag === pill.dataset.tag ? null : pill.dataset.tag;
      document.querySelectorAll('.tag-bar .pill').forEach((p) =>
        p.classList.toggle('active', p.dataset.tag === state.tag),
      );
      applyFilters();
    });
  });

  function applyFilters() {
    let qTag = state.tag;
    let qText = state.search;
    const m = qText.match(/^tag:([a-z0-9_-]+)/);
    if (m) {
      qTag = m[1];
      qText = qText.slice(m[0].length).trim();
    }
    document.querySelectorAll('tr[data-row-kind]').forEach((row) => {
      const mapped = row.dataset.mapped === '1';
      const tags = (row.dataset.tags || '').split(',').filter(Boolean);
      const text = row.textContent.toLowerCase();
      let ok = true;
      if (state.status === 'mapped' && !mapped && row.dataset.rowKind !== 'composite') ok = false;
      if (state.status === 'unmapped' && (mapped || row.dataset.rowKind === 'composite')) ok = false;
      if (qTag && !tags.includes(qTag)) ok = false;
      if (qText && !text.includes(qText)) ok = false;
      row.style.display = ok ? '' : 'none';
    });
    refreshBulkBar();
  }

  // ---------- Selection + adaptive bulk bar ----------
  const bulkbar = document.getElementById('signals-bulkbar');

  function visibleSelected() {
    return [...document.querySelectorAll('tr.row-selectable input.sig-cb:checked')]
      .filter((cb) => cb.closest('tr').style.display !== 'none');
  }

  function classifySelection() {
    const sel = visibleSelected();
    let mapped = 0, unmapped = 0;
    const items = [];
    sel.forEach((cb) => {
      const row = cb.closest('tr');
      const kind = row.dataset.rowKind;
      const isMapped = row.dataset.mapped === '1';
      if (isMapped || kind === 'composite') mapped++;
      else unmapped++;
      items.push({ row, kind, mapped: isMapped, dataset: row.dataset });
    });
    return { mapped, unmapped, items };
  }

  function refreshBulkBar() {
    const { mapped, unmapped } = classifySelection();
    const total = mapped + unmapped;
    bulkbar.classList.toggle('show', total > 0);
    bulkbar.querySelector('[data-bulk="count"]').textContent = total;
    const summary = bulkbar.querySelector('[data-bulk="summary"]');
    const parts = [];
    if (unmapped) parts.push(`${unmapped} unmapped`);
    if (mapped) parts.push(`${mapped} mapped`);
    summary.textContent = parts.join(' · ');
    bulkbar.querySelector('[data-bulk="map-group"]').hidden = !unmapped;
    bulkbar.querySelector('[data-bulk="mapped-groups"]').hidden = !mapped;
    bulkbar.querySelector('[data-bulk="delete"]').hidden = !mapped;
    bulkbar.querySelector('[data-bulk="divider"]').hidden = !(mapped && unmapped);
    bulkbar.querySelector('[data-bulk="map-count"]').textContent = unmapped;
    bulkbar.querySelector('[data-bulk="delete-count"]').textContent = mapped;
  }

  document.querySelectorAll('tr.row-selectable input.sig-cb').forEach((cb) => {
    cb.addEventListener('change', refreshBulkBar);
    cb.addEventListener('click', (e) => e.stopPropagation());
  });

  // Header "select-all" within a card scope
  document.querySelectorAll('input.sig-cb[data-select-all]').forEach((cb) => {
    cb.addEventListener('change', () => {
      const scope = cb.dataset.selectAll;
      document.querySelectorAll(`tr.row-selectable[data-scope="${scope}"] input.sig-cb`).forEach((rowCb) => {
        if (rowCb.closest('tr').style.display === 'none') return;
        rowCb.checked = cb.checked;
      });
      refreshBulkBar();
    });
  });

  // Click row body to toggle
  document.querySelectorAll('tr.row-selectable').forEach((row) => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('input, button, a, .menu, .menu-trigger, .row-meta__signal-name')) return;
      const cb = row.querySelector('input.sig-cb');
      if (cb) {
        cb.checked = !cb.checked;
        refreshBulkBar();
      }
    });
  });

  // ---------- Key expand/collapse ----------
  document.querySelectorAll('.key-row').forEach((kr) => {
    kr.addEventListener('click', (e) => {
      if (e.target.closest('button, a, .menu-trigger')) return;
      const target = document.querySelector(`tr.key-values[data-key-id="${kr.dataset.keyId}"]`);
      if (!target) return;
      const open = !target.hidden;
      target.hidden = open;
      kr.classList.toggle('open', !open);
      if (!open && kr.dataset.lazyLoad === '1') {
        loadKeyValues(kr.dataset.keyId, target);
      }
    });
  });

  async function loadKeyValues(keyId, target) {
    const wrap = target.querySelector('.key-values__wrap');
    if (!wrap || wrap.dataset.loaded === '1') return;
    wrap.innerHTML = '<div class="kv-empty">Loading values…</div>';
    try {
      const r = await fetch(urls.targetingValues(keyId), { credentials: 'same-origin' });
      const data = await r.json();
      if (data.error) {
        wrap.innerHTML = `<div class="kv-empty">${escapeHtml(data.error)}</div>`;
        return;
      }
      const values = data.values || [];
      if (!values.length) {
        wrap.innerHTML = '<div class="kv-empty">No values defined in GAM for this key.</div>';
        return;
      }
      wrap.innerHTML = renderValueTable(keyId, values);
      wrap.dataset.loaded = '1';
    } catch (err) {
      wrap.innerHTML = `<div class="kv-empty">Failed to load values: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderValueTable(keyId, values) {
    const rows = values.map((v) => {
      const sel = `${keyId}:${v.id}`;
      return `<tr class="row-selectable" data-row-kind="kv" data-mapped="0" data-tags="" data-scope="kv-${keyId}" data-sel="${escapeAttr(sel)}">
        <td class="col-check"><input type="checkbox" class="sig-cb" data-sel="${escapeAttr(sel)}" data-key-id="${escapeAttr(keyId)}" data-value-id="${escapeAttr(v.id)}" data-key-name="${escapeAttr(v.key_name || '')}" data-value-name="${escapeAttr(v.display_name || v.name)}"></td>
        <td>
          <div class="row-main">
            <span class="row-main__name"><code>${escapeHtml(v.key_name || keyId)}=${escapeHtml(v.name)}</code></span>
            <span class="row-main__id">gam ${escapeHtml(v.id)}</span>
          </div>
          <div class="row-meta">
            <span class="row-meta__unmapped">Unmapped</span>
            <span style="color: var(--sig-fg-muted)">— tick to expose</span>
          </div>
        </td>
        <td style="width:1px"></td>
      </tr>`;
    }).join('');
    return `<table class="sig-tbl"><tbody>${rows}</tbody></table>`;
  }

  // ---------- Overflow menus ----------
  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('.menu-trigger');
    if (trigger) {
      e.stopPropagation();
      const menu = trigger.nextElementSibling;
      const open = !menu.hidden;
      // Close all other menus
      document.querySelectorAll('.menu').forEach((m) => { if (m !== menu) m.hidden = true; });
      menu.hidden = open;
      return;
    }
    if (!e.target.closest('.menu')) {
      document.querySelectorAll('.menu').forEach((m) => { m.hidden = true; });
    }
  });

  // ---------- Inline rename (signal name in row-meta) ----------
  document.querySelectorAll('.row-meta__signal-name[data-signal-id]').forEach((span) => {
    span.addEventListener('click', (e) => {
      e.stopPropagation();
      if (span.classList.contains('editing')) return;
      startInlineEdit(span);
    });
  });

  function startInlineEdit(span) {
    const original = span.dataset.signalName;
    const signalId = span.dataset.signalId;
    span.classList.add('editing');
    span.innerHTML = `<input type="text" value="${escapeAttr(original)}" style="font: inherit; color: inherit; border: none; background: transparent; outline: none; width: ${Math.max(8, original.length)}ch">`;
    const input = span.querySelector('input');
    input.focus();
    input.select();
    const commit = async () => {
      const newName = input.value.trim();
      span.classList.remove('editing');
      if (!newName || newName === original) {
        span.innerHTML = escapeHtml(original) + pencilSvg();
        return;
      }
      span.innerHTML = `${escapeHtml(newName)}${pencilSvg()} <span style="color: var(--sig-fg-muted); font-size: 11px;">saving…</span>`;
      try {
        const r = await fetch(urls.signalRename(signalId), {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        span.dataset.signalName = newName;
        span.innerHTML = escapeHtml(newName) + pencilSvg();
      } catch (err) {
        alert('Rename failed: ' + err.message);
        span.dataset.signalName = original;
        span.innerHTML = escapeHtml(original) + pencilSvg();
      }
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') {
        span.classList.remove('editing');
        span.innerHTML = escapeHtml(original) + pencilSvg();
      }
    });
    input.addEventListener('click', (e) => e.stopPropagation());
  }

  function pencilSvg() {
    return ' <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle; opacity: 0.55;"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>';
  }

  // ---------- Overflow-menu rename trigger ----------
  document.querySelectorAll('.menu__item[data-action="rename"]').forEach((item) => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      const signalId = item.dataset.signalId;
      const span = document.querySelector(`.row-meta__signal-name[data-signal-id="${CSS.escape(signalId)}"]`);
      if (span) {
        document.querySelectorAll('.menu').forEach((m) => { m.hidden = true; });
        startInlineEdit(span);
      }
    });
  });

  // ---------- Bulk bar actions ----------
  async function postJson(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  // Bulk Create — turn ticked unmapped rows into signals
  bulkbar.querySelector('[data-bulk-action="create"]').addEventListener('click', async () => {
    const { items } = classifySelection();
    const payload = items.filter((it) => !it.mapped && it.kind !== 'composite').map((it) => {
      const d = it.dataset;
      if (it.kind === 'segment') {
        return {
          kind: 'audience_segment',
          segment_id: d.segmentId,
          segment_name: d.segmentName,
        };
      }
      if (it.kind === 'kv') {
        return {
          kind: 'custom_key_value',
          key_id: d.keyId,
          value_id: d.valueId,
          key_name: d.keyName,
          value_name: d.valueName,
        };
      }
      return null;
    }).filter(Boolean);
    if (!payload.length) return;
    try {
      const data = await postJson(urls.bulkCreate, { items: payload });
      alert(`Created ${data.created} signal(s)` + (data.skipped?.length ? `; ${data.skipped.length} already existed` : ''));
      window.location.reload();
    } catch (err) {
      alert('Create failed: ' + err.message);
    }
  });

  function mappedSignalIds() {
    return classifySelection().items
      .filter((it) => it.mapped || it.kind === 'composite')
      .map((it) => it.dataset.signalId)
      .filter(Boolean);
  }

  bulkbar.querySelector('[data-bulk-action="add-tag"]').addEventListener('click', () => bulkTagOp('add_tag'));
  bulkbar.querySelector('[data-bulk-action="remove-tag"]').addEventListener('click', () => bulkTagOp('remove_tag'));
  bulkbar.querySelector('[data-bulk-action="prefix"]').addEventListener('click', () => bulkRenameOp('rename_prefix'));
  bulkbar.querySelector('[data-bulk-action="suffix"]').addEventListener('click', () => bulkRenameOp('rename_suffix'));

  async function bulkTagOp(op) {
    const value = bulkbar.querySelector('[data-bulk-input="tag"]').value.trim();
    const ids = mappedSignalIds();
    if (!value || !ids.length) return;
    try {
      const data = await postJson(urls.bulkUpdate, { signal_ids: ids, op, value });
      alert(`Updated ${data.updated} signal(s)` + (data.skipped?.length ? `; ${data.skipped.length} skipped` : ''));
      window.location.reload();
    } catch (err) {
      alert('Bulk update failed: ' + err.message);
    }
  }

  async function bulkRenameOp(op) {
    const value = bulkbar.querySelector('[data-bulk-input="rename"]').value;
    const ids = mappedSignalIds();
    if (!value || !ids.length) return;
    try {
      const data = await postJson(urls.bulkUpdate, { signal_ids: ids, op, value });
      alert(`Updated ${data.updated} signal(s)` + (data.skipped?.length ? `; ${data.skipped.length} skipped` : ''));
      window.location.reload();
    } catch (err) {
      alert('Bulk rename failed: ' + err.message);
    }
  }

  bulkbar.querySelector('[data-bulk-action="delete"]').addEventListener('click', async () => {
    const ids = mappedSignalIds();
    if (!ids.length) return;
    const referenced = classifySelection().items.filter(
      (it) => (it.mapped || it.kind === 'composite') && parseInt(it.dataset.activeBuys || '0', 10) > 0,
    );
    let confirmTyped = '';
    if (referenced.length) {
      confirmTyped = prompt(
        `${referenced.length} of ${ids.length} selected signal(s) are referenced by active media buys ` +
          `and will break those buys. Type DELETE to confirm:`,
      );
      if (confirmTyped !== 'DELETE') return;
    } else if (!confirm(`Delete ${ids.length} signal(s)?`)) {
      return;
    }
    try {
      const data = await postJson(urls.bulkDelete, { signal_ids: ids, confirm_typed: confirmTyped });
      alert(`Deleted ${data.deleted} signal(s)` + (data.not_found?.length ? `; ${data.not_found.length} not found` : ''));
      window.location.reload();
    } catch (err) {
      alert('Bulk delete failed: ' + err.message);
    }
  });

  bulkbar.querySelector('[data-bulk-action="clear"]').addEventListener('click', () => {
    document.querySelectorAll('tr.row-selectable input.sig-cb:checked').forEach((cb) => { cb.checked = false; });
    refreshBulkBar();
  });

  // ---------- Single-row Map button (one-shot create) ----------
  document.querySelectorAll('[data-action="map-one"]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const d = btn.dataset;
      let item;
      if (d.kind === 'segment') {
        item = { kind: 'audience_segment', segment_id: d.segmentId, segment_name: d.segmentName };
      } else {
        return;
      }
      try {
        const data = await postJson(urls.bulkCreate, { items: [item] });
        if (data.created) window.location.reload();
        else alert('Already mapped.');
      } catch (err) {
        alert('Map failed: ' + err.message);
      }
    });
  });

  // ---------- Overflow-menu delete / unmap ----------
  document.querySelectorAll('.menu__item[data-action="delete"], .menu__item[data-action="unmap"]').forEach((item) => {
    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      const signalId = item.dataset.signalId;
      const activeBuys = parseInt(item.dataset.activeBuys || '0', 10);
      const url = urls.signalDelete(signalId);
      let confirmTyped = '';
      if (activeBuys > 0) {
        confirmTyped = prompt(
          `${signalId} is referenced by ${activeBuys} active media buy(s). Type DELETE to confirm:`,
        );
        if (confirmTyped !== 'DELETE') return;
      } else if (!confirm(`Delete signal "${signalId}"?`)) {
        return;
      }
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = url;
      if (confirmTyped) {
        const inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = 'confirm_typed'; inp.value = confirmTyped;
        form.appendChild(inp);
      }
      document.body.appendChild(form);
      form.submit();
    });
  });

  // ---------- Helpers ----------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]),
    );
  }
  function escapeAttr(s) { return escapeHtml(s); }

  // Initial bulk-bar refresh in case any rows came pre-selected.
  refreshBulkBar();
})();
