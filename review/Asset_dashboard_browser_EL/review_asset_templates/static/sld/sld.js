/* Single Line Diagram tab — ported from dev index.html.
   All DOM IDs are prefixed "sld-*"; all API calls go to "/sld/api/*".
   Initialized lazily when the SLD tab is shown for the first time. */
(function () {
  'use strict';

  const SLD_API = '/sld/api';

  const typeConfig = {
    CDP: { color: '#0d1b3e', shape: 'panel', w: 140, h: 50 },
    NDC: { color: '#0d1b3e', shape: 'panel', w: 140, h: 50 },
    EDC: { color: '#0d1b3e', shape: 'panel', w: 140, h: 50 },
    MDC: { color: '#0d1b3e', shape: 'panel', w: 140, h: 50 },
    MDP: { color: '#0d1b3e', shape: 'panel', w: 140, h: 50 },
    MCC: { color: '#0d1b3e', shape: 'panel', w: 140, h: 50 },
    SWBD:{ color: '#556270', shape: 'switchboard', w: 140, h: 50 },
    ATS: { color: '#1a8a9b', shape: 'ats', w: 140, h: 50 },
    TX:  { color: '#2e6ea6', shape: 'transformer', w: 140, h: 50 },
    PNL: { color: '#4a90c4', shape: 'pnl', w: 140, h: 50 },
    SPL: { color: '#7b68ae', shape: 'splitter', w: 140, h: 50 },
  };

  function getType(tag) {
    const prefix = (tag || '').split('-')[0];
    return typeConfig[prefix] || { color: '#6b7c93', shape: 'rect', w: 110, h: 30 };
  }

  function withRatingUnit(value, unit) {
    const v = String(value == null ? '' : value).trim();
    if (!v) return '';
    // Legacy rows may already carry the unit inside the value (e.g. "208/120V", "208/120VAC");
    // never append a second letter on top of it.
    if (new RegExp(unit + '(?:AC|DC)?$', 'i').test(v)) return v;
    return v + unit;
  }

  function getRatingText(d) {
    const parts = [];
    if (d['Voltage Rating']) parts.push(withRatingUnit(d['Voltage Rating'], 'V'));
    if (d['Amperage Rating']) parts.push(withRatingUnit(d['Amperage Rating'], 'A'));
    if (d['Power Rating']) parts.push(d['Power Rating'] + (d['Power Rating (UoM)'] || ''));
    return parts.join(' | ');
  }

  function hasIdCheckMatch(d) {
    const value = d && d.id_check_match;
    return value === 1 || value === true || value === '1' || String(value).toUpperCase() === 'TRUE';
  }

  function getQrCodeText(d) {
    return (d && (d['QR Code'] || d.qr_code || d.sld_qr_code || '') || '').toString().trim();
  }

  function normalizeSwiftLookupKey(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').toUpperCase();
  }

  function buildEquipmentQrLookup(assets) {
    const lookup = new Map();
    (assets || []).forEach((asset) => {
      const key = normalizeSwiftLookupKey(asset && asset['Equipment ID']);
      if (key && !lookup.has(key)) lookup.set(key, getQrCodeText(asset) || '');
    });
    return lookup;
  }

  function getFedQrCodeText(asset, equipmentQrLookup) {
    const fedFromKey = normalizeSwiftLookupKey(asset && asset['Supply From']);
    if (!fedFromKey) return '';
    const lookup = equipmentQrLookup instanceof Map
      ? equipmentQrLookup
      : buildEquipmentQrLookup(equipmentQrLookup || allAssets || []);
    return lookup.get(fedFromKey) || '';
  }

  function normalizeFindValue(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').toLowerCase();
  }

  function getAssetRowId(asset) {
    if (!asset || asset.row_id === undefined || asset.row_id === null) return '';
    return String(asset.row_id);
  }

  function getFindFieldLabel(mode) {
    return mode === 'qr' ? 'QR Code' : 'Equipment ID';
  }

  function getAssetFindValue(asset, mode) {
    return mode === 'qr' ? getQrCodeText(asset) : ((asset && asset['Equipment ID']) || '');
  }

  // Hover-and-pin photo popover anchored to a QR-code element. One singleton
  // per page; lazily creates its DOM under <body> on first open. Surfaces wire
  // it via attach(triggerEl, qrCode) for plain DOM or attachD3(selection, qrFn)
  // for D3 selections.
  const assetPhotoPopover = (function () {
    let pop = null, img = null, titleEl = null, counterEl = null;
    let prevBtn = null, nextBtn = null, rotBtn = null, zinBtn = null, zoutBtn = null;
    let closeBtn = null, emptyEl = null, loadingEl = null;
    let currentAnchor = null;
    const state = { qr: '', photos: [], idx: 0, scale: 1, rot: 0, pinned: false };
    let openTimer = null, closeTimer = null, abortCtrl = null;
    const cache = new Map();

    function ensureDom() {
      if (pop) return;
      pop = document.createElement('div');
      pop.className = 'sld-photo-popover';
      pop.hidden = true;
      pop.innerHTML = ''
        + '<div class="sld-pp-header">'
        +   '<span class="sld-pp-title"></span>'
        +   '<button type="button" class="sld-pp-close" aria-label="Close">&times;</button>'
        + '</div>'
        + '<div class="sld-pp-stage">'
        +   '<img class="sld-pp-img" alt="" loading="lazy" decoding="async">'
        +   '<div class="sld-pp-empty" hidden>Photo not found</div>'
        +   '<div class="sld-pp-loading" hidden>Loading…</div>'
        + '</div>'
        + '<div class="sld-pp-controls">'
        +   '<button type="button" class="sld-pp-prev" aria-label="Previous photo">&lsaquo;</button>'
        +   '<span class="sld-pp-counter">&mdash;</span>'
        +   '<button type="button" class="sld-pp-next" aria-label="Next photo">&rsaquo;</button>'
        +   '<span class="sld-pp-spacer"></span>'
        +   '<button type="button" class="sld-pp-rot" aria-label="Rotate 90 degrees">&#x21bb;</button>'
        +   '<button type="button" class="sld-pp-zout" aria-label="Zoom out">&minus;</button>'
        +   '<button type="button" class="sld-pp-zin" aria-label="Zoom in">+</button>'
        + '</div>';
      document.body.appendChild(pop);
      titleEl = pop.querySelector('.sld-pp-title');
      img = pop.querySelector('.sld-pp-img');
      emptyEl = pop.querySelector('.sld-pp-empty');
      loadingEl = pop.querySelector('.sld-pp-loading');
      counterEl = pop.querySelector('.sld-pp-counter');
      prevBtn = pop.querySelector('.sld-pp-prev');
      nextBtn = pop.querySelector('.sld-pp-next');
      rotBtn = pop.querySelector('.sld-pp-rot');
      zinBtn = pop.querySelector('.sld-pp-zin');
      zoutBtn = pop.querySelector('.sld-pp-zout');
      closeBtn = pop.querySelector('.sld-pp-close');

      closeBtn.addEventListener('click', (e) => { e.stopPropagation(); close(); });
      prevBtn.addEventListener('click', (e) => { e.stopPropagation(); navigate(-1); });
      nextBtn.addEventListener('click', (e) => { e.stopPropagation(); navigate(+1); });
      rotBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        state.rot = (state.rot + 90) % 360;
        applyTransform();
      });
      zinBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        state.scale = Math.min(4.0, Math.round((state.scale + 0.2) * 10) / 10);
        applyTransform();
      });
      zoutBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        state.scale = Math.max(0.2, Math.round((state.scale - 0.2) * 10) / 10);
        applyTransform();
      });

      // Hover bridge: keep popover open while cursor moves into it.
      pop.addEventListener('mouseenter', () => {
        if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
      });
      pop.addEventListener('mouseleave', () => {
        if (!state.pinned) scheduleClose();
      });

      document.addEventListener('click', (e) => {
        if (!pop || pop.hidden) return;
        if (pop.contains(e.target)) return;
        if (currentAnchor && currentAnchor.contains && currentAnchor.contains(e.target)) return;
        close();
      }, true);
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && pop && !pop.hidden) close();
      });
      window.addEventListener('scroll', () => {
        if (pop && !pop.hidden) close();
      }, true);
    }

    function applyTransform() {
      if (img) img.style.transform = 'scale(' + state.scale + ') rotate(' + state.rot + 'deg)';
    }

    function showPhoto() {
      state.scale = 1; state.rot = 0;
      applyTransform();
      if (!state.photos.length) {
        if (img) { img.removeAttribute('src'); img.hidden = true; }
        if (emptyEl) emptyEl.hidden = false;
        if (loadingEl) loadingEl.hidden = true;
        if (counterEl) counterEl.textContent = '0 / 0';
        if (prevBtn) prevBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
        return;
      }
      const cur = state.photos[state.idx];
      img.hidden = false;
      emptyEl.hidden = true;
      loadingEl.hidden = false;
      img.onload = () => { loadingEl.hidden = true; };
      img.onerror = () => { loadingEl.hidden = true; };
      img.src = cur.url;
      counterEl.textContent = (state.idx + 1) + ' / ' + state.photos.length;
      const multi = state.photos.length > 1;
      prevBtn.disabled = !multi;
      nextBtn.disabled = !multi;
    }

    function navigate(delta) {
      if (!state.photos.length) return;
      const n = state.photos.length;
      state.idx = (state.idx + delta + n) % n;
      showPhoto();
    }

    function position(anchor) {
      if (!pop || !anchor || !anchor.getBoundingClientRect) return;
      const r = anchor.getBoundingClientRect();
      pop.style.visibility = 'hidden';
      pop.hidden = false;
      pop.style.top = '0px';
      pop.style.left = '0px';
      const popR = pop.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let top = r.bottom + 8;
      let left = r.left;
      if (left + popR.width > vw - 8) left = Math.max(8, r.right - popR.width);
      if (top + popR.height > vh - 8) top = Math.max(8, r.top - popR.height - 8);
      if (left < 8) left = 8;
      if (top < 8) top = 8;
      pop.style.top = top + 'px';
      pop.style.left = left + 'px';
      pop.style.visibility = '';
    }

    function fetchPhotos(qr) {
      const cached = cache.get(qr);
      if (cached) return Promise.resolve(cached);
      if (abortCtrl) abortCtrl.abort();
      abortCtrl = new AbortController();
      return fetch(SLD_API + '/photos/' + encodeURIComponent(qr), { signal: abortCtrl.signal })
        .then((r) => {
          if (r.status === 404) return { photos: [], default_idx_in_list: 0 };
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then((data) => { cache.set(qr, data); return data; });
    }

    function open(anchor, qr) {
      if (!qr) return;
      ensureDom();
      if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
      currentAnchor = anchor;
      state.qr = qr;
      titleEl.textContent = qr;
      state.photos = [];
      state.idx = 0;
      showPhoto();
      position(anchor);
      fetchPhotos(qr).then((data) => {
        if (state.qr !== qr) return;
        state.photos = (data && data.photos) || [];
        state.idx = (data && typeof data.default_idx_in_list === 'number') ? data.default_idx_in_list : 0;
        if (state.idx >= state.photos.length) state.idx = 0;
        showPhoto();
        if (currentAnchor === anchor) position(anchor);
      }).catch((err) => {
        if (err && err.name === 'AbortError') return;
        console.warn('[sld] photo fetch failed', err);
        state.photos = [];
        showPhoto();
      });
    }

    function pin() {
      ensureDom();
      state.pinned = true;
      if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
    }

    function close() {
      if (openTimer) { clearTimeout(openTimer); openTimer = null; }
      if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
      if (abortCtrl) { try { abortCtrl.abort(); } catch (e) {} abortCtrl = null; }
      if (!pop) return;
      pop.hidden = true;
      state.pinned = false;
      state.qr = '';
      state.photos = [];
      if (img) img.removeAttribute('src');
      currentAnchor = null;
    }

    function scheduleClose() {
      if (state.pinned) return;
      if (closeTimer) clearTimeout(closeTimer);
      closeTimer = setTimeout(close, 200);
    }

    function scheduleOpen(anchor, qr) {
      if (openTimer) clearTimeout(openTimer);
      if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
      openTimer = setTimeout(() => {
        if (pop && !pop.hidden && state.qr === qr && currentAnchor === anchor) return;
        open(anchor, qr);
      }, 150);
    }

    function attach(triggerEl, qrCode) {
      if (!triggerEl || !qrCode || qrCode === '—') return;
      triggerEl.addEventListener('mouseenter', () => scheduleOpen(triggerEl, qrCode));
      triggerEl.addEventListener('mouseleave', () => {
        if (openTimer) { clearTimeout(openTimer); openTimer = null; }
        scheduleClose();
      });
      triggerEl.addEventListener('click', (e) => {
        e.stopPropagation();
        if (openTimer) { clearTimeout(openTimer); openTimer = null; }
        if (pop && !pop.hidden && state.pinned && currentAnchor === triggerEl) {
          close();
        } else {
          open(triggerEl, qrCode);
          pin();
        }
      });
    }

    function attachD3(selection, qrFn) {
      if (!selection) return;
      selection
        .style('pointer-events', 'auto')
        .style('cursor', 'zoom-in')
        .on('mouseenter.assetphoto', function (event, d) {
          const qr = (qrFn ? qrFn(d) : '') || '';
          if (!qr || qr === '—') return;
          scheduleOpen(this, qr);
        })
        .on('mouseleave.assetphoto', function () {
          if (openTimer) { clearTimeout(openTimer); openTimer = null; }
          scheduleClose();
        })
        .on('click.assetphoto', function (event, d) {
          const qr = (qrFn ? qrFn(d) : '') || '';
          if (!qr || qr === '—') return;
          event.stopPropagation();
          if (openTimer) { clearTimeout(openTimer); openTimer = null; }
          if (pop && !pop.hidden && state.pinned && currentAnchor === this) {
            close();
          } else {
            open(this, qr);
            pin();
          }
        });
    }

    return {
      attach: attach,
      attachD3: attachD3,
      close: close,
      isOpen: () => !!(pop && !pop.hidden),
    };
  })();

  let allAssets = [];
  let missedAssets = [];
  let sdiNotInSldAssets = [];
  let editingAsset = null;
  let currentBuilding = null;
  let selectedFile = null;
  let uploadResult = null;
  let buildingsData = [];
  let statusTimer = null;
  let importBusy = false;
  let pdfPreviewDoc = null;
  let pdfPreviewRenderTask = null;
  let pdfPreviewPage = 1;
  let pdfPreviewScale = 1;
  let pdfPreviewRotation = 0;
  let pdfPreviewReady = false;
  let pdfPreviewToken = 0;
  const PDF_PREVIEW_MIN_SCALE = 0.05;
  const PDF_PREVIEW_MAX_SCALE = 3;
  let initialized = false;
  // Swift Over Room dropdown: Buildings_with_SpaceUID.Location values,
  // cached per-building. See ensureSwiftRoomLocations().
  let swiftRoomLocations = [];
  let swiftRoomLocationsBuilding = null;
  let activeSldView = 'diagram';
  let orientation = loadOrientation();
  let collapsedSet = new Set();
  let collapseInitializedFor = null;
  let activeFindMode = 'equipment';
  let activeFindQuery = '';
  let activeFindResultId = null;
  let activeFindContextIds = new Set();
  let preFocusZoomTransform = null;
  let renderedNodeLookup = new Map();
  let renderedNodesByEquipment = new Map();
  let nodeSelection = null;
  let linkSelection = null;
  let ambiguousLinkSelection = null;
  let findAreaLayer = null;

  let container, svgEl, g, zoomBehavior;

  function loadOrientation() {
    try {
      const v = localStorage.getItem('sld.orientation');
      return (v === 'horizontal') ? 'horizontal' : 'vertical';
    } catch (e) { return 'vertical'; }
  }

  function getCollapseStorageKey(building) {
    return building ? ('sld.collapsed.' + building) : null;
  }

  function loadCollapsedSet(building) {
    const key = getCollapseStorageKey(building);
    if (!key) return null;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? new Set(arr) : null;
    } catch (e) { return null; }
  }

  function persistCollapsedSet() {
    const key = getCollapseStorageKey(currentBuilding);
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify(Array.from(collapsedSet)));
    } catch (e) { /* noop */ }
  }

  function seedDefaultCollapsed(virtualRoot) {
    // Collapse every non-virtual node at depth >= 2 that has children.
    // virtualRoot is depth 0; its real-root children are depth 1; their kids depth 2.
    const seeded = new Set();
    function walk(node, depth) {
      if (!node) return;
      const children = node.children || [];
      if (!node.virtual && depth >= 2 && children.length > 0) {
        seeded.add(node["Equipment ID"]);
      }
      children.forEach(c => walk(c, depth + 1));
    }
    walk(virtualRoot, 0);
    return seeded;
  }

  function ensureCollapseStateForBuilding(virtualRoot) {
    if (collapseInitializedFor === currentBuilding) return;
    const stored = loadCollapsedSet(currentBuilding);
    if (stored) {
      collapsedSet = stored;
    } else {
      collapsedSet = seedDefaultCollapsed(virtualRoot);
      persistCollapsedSet();
    }
    collapseInitializedFor = currentBuilding;
  }

  function countSubtreeNodes(data) {
    if (!data || !data.children) return 0;
    let n = 0;
    for (const c of data.children) n += 1 + countSubtreeNodes(c);
    return n;
  }

  function toggleCollapseFor(equipmentId) {
    if (!equipmentId) return;
    if (collapsedSet.has(equipmentId)) collapsedSet.delete(equipmentId);
    else collapsedSet.add(equipmentId);
    persistCollapsedSet();
    if (allAssets && allAssets.length) {
      renderDiagram(allAssets);
      if (!focusActiveSearchIfPossible()) fitToScreen();
    }
  }

  function expandAll() {
    collapsedSet = new Set();
    persistCollapsedSet();
    if (allAssets && allAssets.length) {
      renderDiagram(allAssets);
      if (!focusActiveSearchIfPossible()) fitToScreen();
    }
  }

  function collapseToLevel2() {
    if (!allAssets || !allAssets.length) return;
    const virtualRoot = buildTree(allAssets);
    collapsedSet = seedDefaultCollapsed(virtualRoot);
    persistCollapsedSet();
    renderDiagram(allAssets);
    if (!focusActiveSearchIfPossible()) fitToScreen();
  }

  function $(id) { return document.getElementById(id); }

  function init() {
    if (initialized) return;
    if (typeof d3 === 'undefined') {
      console.warn('[sld] D3 not loaded yet, deferring init');
      return;
    }
    container = $('sld-diagram');
    if (!container) return;

    svgEl = d3.select('#sld-diagram').append('svg');
    g = svgEl.append('g');

    zoomBehavior = d3.zoom()
      .scaleExtent([0.1, 3])
      .on('zoom', (event) => {
        g.attr('transform', event.transform);
        updateZoomLabel(event.transform.k);
      });
    svgEl.call(zoomBehavior);

    bindUIEvents();
    updateFindControls();
    updateOrientationButton();
    switchSldView(activeSldView);
    initialized = true;

    try {
      var qs = new URLSearchParams(window.location.search);
      var urlBuilding = qs.get('filter_building') || qs.get('building');
      if (urlBuilding) { currentBuilding = urlBuilding; }
    } catch (e) { /* noop */ }

    loadBuildings();
  }

  function bindUIEvents() {
    $('sld-building-selector').addEventListener('change', (e) => {
      currentBuilding = e.target.value;
      collapsedSet = new Set();
      collapseInitializedFor = null;
      loadBuildingAssets(currentBuilding);
    });
    $('sld-find-mode').addEventListener('change', onFindModeChange);
    $('sld-find-input').addEventListener('input', onFindInput);
    $('sld-find-input').addEventListener('keydown', onFindInputKeydown);
    $('sld-find-clear-btn').addEventListener('click', () => clearFindSearch({ restoreViewport: true }));
    $('sld-find-results').addEventListener('click', onFindResultsClick);

    $('sld-open-import-btn').addEventListener('click', openImportPanel);
    $('sld-empty-import-btn').addEventListener('click', openImportPanel);
    $('sld-open-create-btn').addEventListener('click', openCreatePanel);
    $('sld-orientation-btn').addEventListener('click', toggleOrientation);
    const collapseL2Btn = $('sld-collapse-l2-btn');
    if (collapseL2Btn) collapseL2Btn.addEventListener('click', collapseToLevel2);
    const expandAllBtn = $('sld-expand-all-btn');
    if (expandAllBtn) expandAllBtn.addEventListener('click', expandAll);
    const exportPdfBtn = $('sld-export-pdf-btn');
    if (exportPdfBtn) exportPdfBtn.addEventListener('click', exportSldPdf);
    const exportXlsxBtn = $('sld-export-xlsx-btn');
    if (exportXlsxBtn) exportXlsxBtn.addEventListener('click', exportSwiftExcel);
    const swiftToggle = $('sld-swift-over-toggle');
    if (swiftToggle) {
      swiftToggle.addEventListener('change', (e) => {
        if (e.target.checked) openSwiftOver();
        else closeSwiftOver();
      });
    }
    $('sld-zoom-in-btn').addEventListener('click', () => zoomBy(1.25));
    $('sld-zoom-out-btn').addEventListener('click', () => zoomBy(0.8));
    $('sld-zoom-fit-btn').addEventListener('click', fitToScreen);
    $('sld-zoom-reset-btn').addEventListener('click', resetZoom);
    const swiftTabs = $('sld-swift-view-tabs');
    if (swiftTabs) {
      swiftTabs.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-sld-swift-view]');
        if (!btn || !swiftTabs.contains(btn)) return;
        e.preventDefault();
        switchSwiftView(btn.dataset.sldSwiftView);
      });
    }
    $('sld-missed-select-all').addEventListener('change', (e) => toggleAllMissedRows(e.target.checked));
    $('sld-exclude-missed-btn').addEventListener('click', excludeSelectedMissedAssets);

    $('sld-ep-close-btn').addEventListener('click', closeEditPanel);
    $('sld-ep-cancel').addEventListener('click', closeEditPanel);
    $('sld-ep-save').addEventListener('click', saveAsset);
    $('sld-ep-delete').addEventListener('click', deleteAsset);

    $('sld-cp-close-btn').addEventListener('click', closeCreatePanel);
    $('sld-cp-cancel').addEventListener('click', closeCreatePanel);
    $('sld-cp-save').addEventListener('click', createAsset);
    $('sld-cp-qr').addEventListener('input', onCreateQrInput);

    $('sld-import-close-btn').addEventListener('click', closeImportPanel);
    $('sld-import-cancel').addEventListener('click', closeImportPanel);
    $('sld-import-btn').addEventListener('click', startImport);
    $('sld-pdf-prev-btn').addEventListener('click', () => changePdfPreviewPage(-1));
    $('sld-pdf-next-btn').addEventListener('click', () => changePdfPreviewPage(1));
    $('sld-pdf-zoom-out-btn').addEventListener('click', () => zoomPdfPreview(0.8));
    $('sld-pdf-zoom-in-btn').addEventListener('click', () => zoomPdfPreview(1.25));
    $('sld-pdf-rotate-btn').addEventListener('click', rotatePdfPreview);
    bindPdfPreviewPan();

    const dropZone = $('sld-drop-zone');
    dropZone.addEventListener('click', () => $('sld-file-input').click());
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault(); dropZone.classList.remove('drag-over');
      if (e.dataTransfer.files.length > 0) onFileSelected(e.dataTransfer.files[0]);
    });

    $('sld-file-input').addEventListener('change', (e) => {
      if (e.target.files.length > 0) onFileSelected(e.target.files[0]);
    });
    $('sld-file-clear-btn').addEventListener('click', clearSelectedFile);

    $('sld-edit-overlay').addEventListener('click', (e) => { if (e.target.id === 'sld-edit-overlay') closeEditPanel(); });
    $('sld-create-overlay').addEventListener('click', (e) => { if (e.target.id === 'sld-create-overlay') closeCreatePanel(); });
    $('sld-import-overlay').addEventListener('click', (e) => { if (e.target.id === 'sld-import-overlay') closeImportPanel(); });
    $('sld-missed-qr-overlay').addEventListener('click', (e) => { if (e.target.id === 'sld-missed-qr-overlay') closeMissedQrModal(); });
    $('sld-mqr-close-btn').addEventListener('click', closeMissedQrModal);
    $('sld-mqr-cancel').addEventListener('click', closeMissedQrModal);
    $('sld-mqr-confirm').addEventListener('click', onMissedQrConfirm);
    $('sld-mqr-qr').addEventListener('input', onMissedQrInput);
    $('sld-mqr-qr').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const confirmBtn = $('sld-mqr-confirm');
        if (confirmBtn && !confirmBtn.disabled) onMissedQrConfirm();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && isAnySldModalOpen()) {
        closeEditPanel(); closeCreatePanel(); closeImportPanel(); closeMissedQrModal();
        return;
      }
      if (e.key === 'Escape' && hasActiveFindState()) {
        clearFindSearch({ restoreViewport: true });
        e.preventDefault();
        return;
      }
      if (!isSldTabActive() || isAnySldModalOpen()) return;
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === '+' || e.key === '=') { zoomBy(1.25); e.preventDefault(); }
      else if (e.key === '-' || e.key === '_') { zoomBy(0.8); e.preventDefault(); }
      else if (e.key === '0') { fitToScreen(); e.preventDefault(); }
    });

    window.addEventListener('resize', () => {
      if (svgEl && container) svgEl.attr('width', container.clientWidth).attr('height', container.clientHeight);
    });
  }

  function hasActiveFindState() {
    return !!(normalizeFindValue(activeFindQuery) || activeFindResultId || preFocusZoomTransform);
  }

  function updateFindInputPlaceholder() {
    const input = $('sld-find-input');
    if (!input) return;
    input.placeholder = activeFindMode === 'qr'
      ? 'Enter a QR Code'
      : 'Enter an Equipment ID';
  }

  function updateFindControls() {
    const mode = $('sld-find-mode');
    const clearBtn = $('sld-find-clear-btn');
    if (mode) mode.value = activeFindMode;
    if (clearBtn) clearBtn.disabled = !hasActiveFindState();
    updateFindInputPlaceholder();
  }

  function onFindModeChange(e) {
    activeFindMode = e && e.target && e.target.value === 'qr' ? 'qr' : 'equipment';
    activeFindResultId = null;
    activeFindContextIds = new Set();
    syncFindState({ refocus: true, restoreViewportOnEmpty: false });
  }

  function onFindInput(e) {
    activeFindQuery = e && e.target ? e.target.value : '';
    activeFindResultId = null;
    activeFindContextIds = new Set();
    syncFindState({ refocus: true, restoreViewportOnEmpty: true });
  }

  function onFindInputKeydown(e) {
    if (e.key === 'Escape') {
      clearFindSearch({ restoreViewport: true });
      e.preventDefault();
      return;
    }
    if (e.key !== 'Enter') return;
    const firstResult = document.querySelector('#sld-find-results .sld-find-result');
    if (!firstResult) return;
    firstResult.click();
    e.preventDefault();
  }

  function onFindResultsClick(e) {
    const btn = e.target.closest('.sld-find-result');
    if (!btn) return;
    const asset = findAssetByRowId(btn.dataset.rowId);
    if (!asset) return;
    focusFindResult(asset, { updateInput: true, fitViewport: true, preserveExistingPreFocus: true });
  }

  function setFindStatus(message, kind) {
    const el = $('sld-find-status');
    if (!el) return;
    el.textContent = message || '';
    el.className = 'sld-find-status' + (kind ? ' ' + kind : '');
    el.hidden = !message;
  }

  function renderFindResults(matches) {
    const list = $('sld-find-results');
    if (!list) return;
    list.innerHTML = '';
    const items = (matches || []).slice(0, 8);
    if (!items.length) {
      list.hidden = true;
      return;
    }
    const modeLabel = getFindFieldLabel(activeFindMode);
    items.forEach((asset) => {
      const rowId = getAssetRowId(asset);
      const isActive = rowId && rowId === String(activeFindResultId || '');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'sld-find-result' + (isActive ? ' is-active' : '');
      button.dataset.rowId = rowId;
      const room = asset['Room'] ? `Room ${asset['Room']}` : 'Room unavailable';
      const parent = asset['Supply From'] ? `Fed from ${asset['Supply From']}` : 'Root level';
      const qrCode = getQrCodeText(asset) || 'No QR Code';
      const primary = activeFindMode === 'qr' ? (getQrCodeText(asset) || '(no QR Code)') : (asset['Equipment ID'] || '(no Equipment ID)');
      const secondary = activeFindMode === 'qr' ? (asset['Equipment ID'] || '(no Equipment ID)') : qrCode;
      button.innerHTML =
        `<span class="sld-find-result-title">` +
          `<span>${escapeHtml(primary)}</span>` +
          `<span class="sld-find-result-mode">${escapeHtml(modeLabel)}</span>` +
        `</span>` +
        `<span class="sld-find-result-meta">${escapeHtml(secondary)} · Level ${escapeHtml(String(asset.Hierarchy != null ? asset.Hierarchy : '—'))} · ${escapeHtml(room)} · ${escapeHtml(parent)}</span>`;
      list.appendChild(button);
    });
    list.hidden = false;
  }

  function evaluateFindMatches(query, mode) {
    const normalizedQuery = normalizeFindValue(query);
    const exact = [];
    const partial = [];
    if (!normalizedQuery) return { exact, partial };
    (allAssets || []).forEach((asset) => {
      const value = normalizeFindValue(getAssetFindValue(asset, mode));
      if (!value) return;
      if (value === normalizedQuery) exact.push(asset);
      else if (value.includes(normalizedQuery)) partial.push(asset);
    });
    const sorter = (a, b) => {
      const ah = Number(a && a.Hierarchy);
      const bh = Number(b && b.Hierarchy);
      const hierarchyDiff = (Number.isFinite(ah) ? ah : Number.MAX_SAFE_INTEGER) - (Number.isFinite(bh) ? bh : Number.MAX_SAFE_INTEGER);
      if (hierarchyDiff) return hierarchyDiff;
      return String((a && a['Equipment ID']) || '').localeCompare(String((b && b['Equipment ID']) || ''));
    };
    exact.sort(sorter);
    partial.sort(sorter);
    return { exact, partial };
  }

  function findAssetByRowId(rowId) {
    const key = rowId == null ? '' : String(rowId);
    if (!key) return null;
    return (allAssets || []).find(asset => getAssetRowId(asset) === key) || null;
  }

  function capturePreFocusZoom() {
    if (!svgEl || preFocusZoomTransform) return;
    const transform = d3.zoomTransform(svgEl.node());
    preFocusZoomTransform = { x: transform.x, y: transform.y, k: transform.k };
  }

  function restorePreFocusZoom() {
    if (preFocusZoomTransform) {
      const transform = d3.zoomIdentity
        .translate(preFocusZoomTransform.x, preFocusZoomTransform.y)
        .scale(preFocusZoomTransform.k);
      svgEl.transition().duration(450).call(zoomBehavior.transform, transform);
      preFocusZoomTransform = null;
      return true;
    }
    return false;
  }

  function getFindContextIds(rowId) {
    const contextIds = new Set();
    const key = rowId == null ? '' : String(rowId);
    if (!key) return contextIds;
    const info = renderedNodeLookup.get(key);
    if (info) {
      if (info.parentRowId) contextIds.add(info.parentRowId);
      (info.childRowIds || []).forEach((childId) => {
        if (childId) contextIds.add(String(childId));
      });
      return contextIds;
    }
    const asset = findAssetByRowId(key);
    if (!asset) return contextIds;
    const parentTag = (asset['Supply From'] || '').trim();
    if (parentTag) {
      const parentAsset = (allAssets || []).find(candidate => (candidate['Equipment ID'] || '').trim() === parentTag);
      if (parentAsset) contextIds.add(getAssetRowId(parentAsset));
    }
    (allAssets || []).forEach((candidate) => {
      if ((candidate['Supply From'] || '').trim() === (asset['Equipment ID'] || '').trim()) {
        const childId = getAssetRowId(candidate);
        if (childId) contextIds.add(childId);
      }
    });
    return contextIds;
  }

  function clearFindSelection(options) {
    const restoreViewport = !!(options && options.restoreViewport);
    const preservePreFocus = !!(options && options.preservePreFocus);
    activeFindResultId = null;
    activeFindContextIds = new Set();
    applyFindVisualState();
    syncSwiftFindHighlight();
    if (restoreViewport && !restorePreFocusZoom() && allAssets && allAssets.length && activeSldView === 'diagram' && !swiftActive) {
      fitToScreen();
    }
    if (!preservePreFocus) preFocusZoomTransform = null;
    updateFindControls();
  }

  function clearFindSearch(options) {
    activeFindQuery = '';
    activeFindResultId = null;
    activeFindContextIds = new Set();
    const input = $('sld-find-input');
    if (input) input.value = '';
    renderFindResults([]);
    setFindStatus('', '');
    clearFindSelection({ restoreViewport: !!(options && options.restoreViewport), preservePreFocus: false });
  }

  function syncFindState(options) {
    updateFindControls();
    const query = normalizeFindValue(activeFindQuery);
    if (!query) {
      renderFindResults([]);
      setFindStatus('', '');
      clearFindSelection({
        restoreViewport: !!(options && options.restoreViewportOnEmpty),
        preservePreFocus: false
      });
      return false;
    }
    if (!allAssets || !allAssets.length) {
      clearFindSelection({ restoreViewport: false, preservePreFocus: true });
      renderFindResults([]);
      setFindStatus(`No ${getFindFieldLabel(activeFindMode)} match in this diagram.`, 'warn');
      return false;
    }

    const selectedAsset = findAssetByRowId(activeFindResultId);
    if (selectedAsset && normalizeFindValue(getAssetFindValue(selectedAsset, activeFindMode)) === query) {
      activeFindContextIds = getFindContextIds(activeFindResultId);
      applyFindVisualState();
      syncSwiftFindHighlight();
      renderFindResults([]);
      setFindStatus('', '');
      if (options && options.refocus && activeSldView === 'diagram' && !swiftActive) fitToFocusArea(activeFindResultId);
      return true;
    }

    const matches = evaluateFindMatches(activeFindQuery, activeFindMode);
    if (matches.exact.length === 1) {
      return focusFindResult(matches.exact[0], {
        updateInput: false,
        fitViewport: !!(options && options.refocus),
        preserveExistingPreFocus: true
      });
    }

    clearFindSelection({ restoreViewport: false, preservePreFocus: true });
    if (matches.exact.length > 1) {
      setFindStatus('', '');
      renderFindResults(matches.exact);
      return false;
    }
    if (matches.partial.length > 0) {
      setFindStatus('', '');
      renderFindResults(matches.partial);
      return false;
    }

    renderFindResults([]);
    setFindStatus(`No ${getFindFieldLabel(activeFindMode)} match in this diagram.`, 'warn');
    return false;
  }

  function focusFindResult(asset, options) {
    if (!asset) return false;
    if (swiftActive) {
      closeSwiftOver({
        preserveViewport: true,
        afterShown: () => focusFindResult(asset, options)
      });
      return true;
    }
    const rowId = getAssetRowId(asset);
    if (!rowId) return false;
    if (!(options && options.preserveExistingPreFocus) || !preFocusZoomTransform) capturePreFocusZoom();
    activeFindResultId = rowId;
    activeFindContextIds = getFindContextIds(rowId);
    if (!options || options.updateInput !== false) {
      activeFindQuery = getAssetFindValue(asset, activeFindMode);
      const input = $('sld-find-input');
      if (input) input.value = activeFindQuery;
    }
    renderFindResults([]);
    setFindStatus('', '');
    updateFindControls();
    applyFindVisualState();
    syncSwiftFindHighlight();
    if (!options || options.fitViewport !== false) fitToFocusArea(rowId);
    return true;
  }

  function focusActiveSearchIfPossible() {
    return syncFindState({ refocus: true, restoreViewportOnEmpty: false });
  }

  function isAnySldModalOpen() {
    return document.querySelector('.sld-pane .sld-modal-overlay.open') !== null;
  }

  let activeSwiftView = 'swift-table';
  function switchSwiftView(view) {
    const valid = { 'swift-table': 1, missed: 1, 'sdi-missing': 1 };
    activeSwiftView = valid[view] ? view : 'swift-table';
    const views = {
      'swift-table': $('sld-swift-table-view'),
      missed: $('sld-missed-view'),
      'sdi-missing': $('sld-sdi-missing-view'),
    };
    const buttons = {
      'swift-table': $('sld-view-swift-table-btn'),
      missed: $('sld-view-missed-btn'),
      'sdi-missing': $('sld-view-sdi-missing-btn'),
    };
    Object.keys(views).forEach((name) => {
      const isActive = name === activeSwiftView;
      if (views[name]) {
        views[name].classList.toggle('active', isActive);
        views[name].style.display = isActive ? 'block' : 'none';
        views[name].setAttribute('aria-hidden', isActive ? 'false' : 'true');
      }
      if (buttons[name]) {
        buttons[name].classList.toggle('active', isActive);
        buttons[name].setAttribute('aria-selected', isActive ? 'true' : 'false');
        buttons[name].setAttribute('tabindex', isActive ? '0' : '-1');
      }
    });
  }

  function switchSldView(view, options) {
    activeSldView = 'diagram';
    const diagramView = $('sld-diagram-view');
    if (diagramView) {
      diagramView.classList.add('active');
      diagramView.setAttribute('aria-hidden', 'false');
    }
    setTimeout(() => {
      if (!svgEl || !container) return;
      svgEl.attr('width', container.clientWidth).attr('height', container.clientHeight);
      if (options && typeof options.afterShown === 'function') {
        options.afterShown();
        return;
      }
      if (!(options && options.preserveViewport)) fitToScreen();
    }, 0);
  }

  function getGlobalSelectedDisplay() {
    // Dashboard building control is the BuildingMultiselect (global-building-ms);
    // its toggle text is the display name when exactly one building is selected
    // (the only state in which the SLD pane is reachable).
    var ms = window.globalBuildingMs;
    if (ms && typeof ms.values === 'function' && ms.values().length === 1) {
      var msRoot = document.getElementById('global-building-ms');
      var msText = msRoot ? msRoot.querySelector('.ms-text') : null;
      var msTxt = msText ? (msText.textContent || '').trim() : '';
      if (msTxt) return msTxt;
    }
    // Legacy fallback: the old single <select> (kept for old cached pages).
    var globalSel = document.getElementById('global-building-selector');
    if (globalSel && globalSel.selectedOptions && globalSel.selectedOptions[0]) {
      var txt = (globalSel.selectedOptions[0].textContent || '').trim();
      if (txt && globalSel.selectedOptions[0].value) return txt;
    }
    return null;
  }

  async function loadBuildings() {
    try {
      const resp = await fetch(SLD_API + '/buildings');
      if (!resp.ok) {
        const errorResult = await resp.json();
        showToast(errorResult.error || `Request failed (${resp.status})`, 'error');
        return;
      }
      const buildings = await resp.json();
      buildingsData = buildings || [];
      const sel = $('sld-building-selector');
      sel.innerHTML = '';

      (buildings || []).forEach((b) => {
        const opt = document.createElement('option');
        opt.value = b.building;
        opt.textContent = `${b.display || b.building} (${b.current_count} assets)`;
        sel.appendChild(opt);
      });

      if (!currentBuilding) {
        if (buildings && buildings.length > 0) {
          currentBuilding = buildings[0].building;
        } else {
          sel.innerHTML = '<option value="">No buildings</option>';
          showEmptyState();
          return;
        }
      } else if (!sel.querySelector('option[value="' + CSS.escape(currentBuilding) + '"]')) {
        const opt = document.createElement('option');
        opt.value = currentBuilding;
        opt.textContent = getGlobalSelectedDisplay() || currentBuilding;
        sel.appendChild(opt);
      }

      sel.value = currentBuilding;
      loadBuildingAssets(currentBuilding);
    } catch (e) {
      console.error('[sld] Failed to load buildings:', e);
      showToast('Failed to load buildings', 'error');
    }
  }

  async function loadBuildingAssets(building) {
    currentBuilding = building;
    // Refresh the Swift Over Room dropdown in parallel with the asset fetch
    // so renderSwiftTable sees the right list when swift is active. Awaited
    // below before renderDiagramOrEmpty runs renderSwiftTable.
    const locationsPromise = ensureSwiftRoomLocations(building);
    const match = (buildingsData || []).find(x => x.building === building);
    const display = (match && match.display) || getGlobalSelectedDisplay() || building;
    $('sld-page-title').textContent = `${display} \u2014 Single Line Diagram`;
    try {
      const resp = await fetch(
        SLD_API + '/assets?building=' + encodeURIComponent(building) + '&_=' + Date.now(),
        { cache: 'no-store' }
      );
      if (!resp.ok) {
        const errorResult = await resp.json();
        showToast(errorResult.error || `Request failed (${resp.status})`, 'error');
        return;
      }
      const data = await resp.json();
      await locationsPromise;
      renderDiagramOrEmpty(data || []);
      await loadMissedAssets(building);
      await loadSdiNotInSldAssets(building);
      if (data && data.length > 0 && !focusActiveSearchIfPossible()) fitToScreen();
    } catch (e) {
      showToast('Failed to load assets: ' + e.message, 'error');
    }
  }

  function showEmptyState() {
    allAssets = [];
    renderedNodeLookup = new Map();
    renderedNodesByEquipment = new Map();
    nodeSelection = null;
    linkSelection = null;
    ambiguousLinkSelection = null;
    findAreaLayer = null;
    $('sld-empty-state').style.display = 'flex';
    if (g) g.selectAll('*').remove();
    updateAssetStats();
    syncFindState({ refocus: false, restoreViewportOnEmpty: false });
  }

  async function loadMissedAssets(building) {
    try {
      const resp = await fetch(SLD_API + '/missed-assets?building=' + encodeURIComponent(building || ''));
      if (!resp.ok) {
        const errorResult = await resp.json();
        showToast(errorResult.error || `Request failed (${resp.status})`, 'error');
        return;
      }
      const data = await resp.json();
      renderMissedAssets(data || []);
    } catch (e) {
      showToast('Failed to load missed assets: ' + e.message, 'error');
    }
  }

  function renderMissedAssets(assets) {
    missedAssets = assets || [];
    const tbody = $('sld-missed-tbody');
    const selectAll = $('sld-missed-select-all');
    tbody.innerHTML = '';
    selectAll.checked = false;
    selectAll.disabled = missedAssets.length === 0;

    if (!missedAssets.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="sld-missed-empty">No missed assets.</td></tr>';
      updateMissedSelectionState();
      updateAssetStats();
      return;
    }

    missedAssets.forEach(asset => {
      const tr = document.createElement('tr');
      const rowId = String(asset.row_id);
      tr.innerHTML =
        `<td class="sld-select-col"><input type="checkbox" class="sld-missed-row-check" value="${escapeHtml(rowId)}" aria-label="Select ${escapeHtml(asset['Equipment ID'] || 'asset')}"></td>` +
        `<td>${escapeHtml(asset['Equipment ID'] || '')}</td>` +
        `<td>${escapeHtml(asset['Supply From'] || '')}</td>` +
        `<td>${escapeHtml(asset['Group of Asset'] || 'Unknown')}</td>` +
        `<td class="sld-action-col"><button type="button" class="ep-btn ep-btn-create sld-missed-add-btn" data-row-id="${escapeHtml(rowId)}">Add to SLD</button></td>`;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.sld-missed-row-check').forEach(cb => {
      cb.addEventListener('change', updateMissedSelectionState);
    });
    tbody.querySelectorAll('.sld-missed-add-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const rowId = Number(btn.dataset.rowId);
        if (!Number.isFinite(rowId)) return;
        const asset = (missedAssets || []).find(a => Number(a.row_id) === rowId);
        const equipmentId = (asset && asset['Equipment ID']) || '';
        openMissedQrModal(rowId, btn, equipmentId);
      });
    });
    updateMissedSelectionState();
    updateAssetStats();
  }

  function toggleAllMissedRows(checked) {
    document.querySelectorAll('#sld-missed-tbody .sld-missed-row-check').forEach(cb => { cb.checked = checked; });
    updateMissedSelectionState();
  }

  function updateMissedSelectionState() {
    const checks = Array.from(document.querySelectorAll('#sld-missed-tbody .sld-missed-row-check'));
    const selected = checks.filter(cb => cb.checked);
    const selectAll = $('sld-missed-select-all');
    const excludeBtn = $('sld-exclude-missed-btn');
    if (selectAll) {
      selectAll.checked = checks.length > 0 && selected.length === checks.length;
      selectAll.indeterminate = selected.length > 0 && selected.length < checks.length;
      selectAll.disabled = checks.length === 0;
    }
    if (excludeBtn) excludeBtn.disabled = selected.length === 0;
  }

  async function excludeSelectedMissedAssets() {
    const rowIds = Array.from(document.querySelectorAll('#sld-missed-tbody .sld-missed-row-check:checked'))
      .map(cb => Number(cb.value))
      .filter(Number.isFinite);
    if (!rowIds.length) return;
    if (!confirm(`Exclude ${rowIds.length} missed asset(s) from the active SLD process?`)) return;

    const btn = $('sld-exclude-missed-btn');
    btn.disabled = true;
    btn.textContent = 'Excluding...';
    try {
      const resp = await fetch(SLD_API + '/missed-assets/exclude', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ building: currentBuilding || '', row_ids: rowIds })
      });
      const result = await resp.json();
      if (!resp.ok) {
        showToast(result.error || 'Exclude failed', 'error');
        return;
      }
      renderDiagramOrEmpty(result.assets || []);
      renderMissedAssets(result.missed_assets || []);
      if (result.sdi_not_in_sld !== undefined) renderSdiNotInSldAssets(result.sdi_not_in_sld || []);
      loadBuildings();
      if (activeSldView === 'diagram' && !focusActiveSearchIfPossible()) fitToScreen();
      showToast(`Excluded ${rowIds.length} missed asset(s)`, 'success');
    } catch (e) {
      showToast('Network error: ' + e.message, 'error');
    } finally {
      btn.textContent = 'Exclude selected';
      updateMissedSelectionState();
    }
  }

  async function addMissedAssetToSld(rowId, btn, qrCode) {
    const previousText = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Adding...';
    }
    try {
      const resp = await fetch(SLD_API + '/missed-assets/add-to-sld', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ building: currentBuilding || '', row_id: rowId, qr_code: qrCode || '' })
      });
      const result = await resp.json();
      if (!resp.ok) {
        showToast(result.error || 'Add to SLD failed', 'error');
        if (btn) {
          btn.disabled = false;
          btn.textContent = previousText || 'Add to SLD';
        }
        return false;
      }

      renderDiagramOrEmpty(result.assets || []);
      renderMissedAssets(result.missed_assets || []);
      if (result.sdi_not_in_sld !== undefined) renderSdiNotInSldAssets(result.sdi_not_in_sld || []);
      loadBuildings();
      showToast('Added asset to SLD', 'success');
      return true;
    } catch (e) {
      showToast('Network error: ' + e.message, 'error');
      if (btn) {
        btn.disabled = false;
        btn.textContent = previousText || 'Add to SLD';
      }
      return false;
    }
  }

  // ── Missed-asset QR prompt ───────────────────────────────────────────────
  let missedQrPending = null;      // { rowId, btn }
  let missedQrValidateSeq = 0;
  let missedQrValidateTimer = null;

  function openMissedQrModal(rowId, btn, equipmentId) {
    missedQrPending = { rowId, btn };
    const eqEl = $('sld-mqr-equipment');
    if (eqEl) eqEl.textContent = equipmentId || '(unknown)';
    const input = $('sld-mqr-qr');
    if (input) input.value = '';
    setMissedQrStatus('', '');
    const confirmBtn = $('sld-mqr-confirm');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Confirm & Add'; }
    $('sld-missed-qr-overlay').classList.add('open');
    if (input) setTimeout(() => input.focus(), 0);
  }

  function closeMissedQrModal() {
    $('sld-missed-qr-overlay').classList.remove('open');
    missedQrPending = null;
    missedQrValidateSeq++; // invalidate any in-flight validations
    if (missedQrValidateTimer) { clearTimeout(missedQrValidateTimer); missedQrValidateTimer = null; }
  }

  function setMissedQrStatus(msg, kind) {
    const el = $('sld-mqr-status');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'sld-mqr-status' + (kind ? ' ' + kind : '');
  }

  function onMissedQrInput() {
    const input = $('sld-mqr-qr');
    const confirmBtn = $('sld-mqr-confirm');
    if (!input || !confirmBtn) return;
    const raw = input.value || '';
    confirmBtn.disabled = true;
    if (!raw.trim()) {
      setMissedQrStatus('', '');
      if (missedQrValidateTimer) { clearTimeout(missedQrValidateTimer); missedQrValidateTimer = null; }
      return;
    }
    setMissedQrStatus('Checking…', 'pending');
    if (missedQrValidateTimer) clearTimeout(missedQrValidateTimer);
    missedQrValidateTimer = setTimeout(() => runMissedQrValidate(raw), 250);
  }

  async function runMissedQrValidate(raw) {
    const seq = ++missedQrValidateSeq;
    const building = currentBuilding || '';
    if (!building) {
      setMissedQrStatus('No building selected', 'error');
      return;
    }
    try {
      const url = SLD_API + '/qr-codes/validate?qr=' + encodeURIComponent(raw)
                + '&building=' + encodeURIComponent(building);
      const resp = await fetch(url, { cache: 'no-store' });
      if (seq !== missedQrValidateSeq) return; // stale
      const data = await resp.json().catch(() => ({}));
      if (seq !== missedQrValidateSeq) return;
      const confirmBtn = $('sld-mqr-confirm');
      if (data && data.valid) {
        setMissedQrStatus('OK — QR Exist In the Asset Capture Application', 'ok');
        if (confirmBtn) confirmBtn.disabled = false;
      } else {
        setMissedQrStatus((data && data.error) || 'QR Code not valid for this building', 'error');
        if (confirmBtn) confirmBtn.disabled = true;
      }
    } catch (e) {
      if (seq !== missedQrValidateSeq) return;
      setMissedQrStatus('Network error: ' + (e && e.message ? e.message : e), 'error');
      const confirmBtn = $('sld-mqr-confirm');
      if (confirmBtn) confirmBtn.disabled = true;
    }
  }

  async function onMissedQrConfirm() {
    if (!missedQrPending) return;
    const input = $('sld-mqr-qr');
    const confirmBtn = $('sld-mqr-confirm');
    const qr = (input && input.value || '').trim();
    if (!qr) return;
    const { rowId, btn } = missedQrPending;
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Adding…'; }
    const ok = await addMissedAssetToSld(rowId, btn, qr);
    if (ok) {
      closeMissedQrModal();
    } else if (confirmBtn) {
      confirmBtn.textContent = 'Confirm & Add';
      confirmBtn.disabled = false;
    }
  }

  // ── Create-asset QR prompt ───────────────────────────────────────────────
  let createQrValidateSeq = 0;
  let createQrValidateTimer = null;
  let createQrIsValid = false;

  function setCreateQrStatus(msg, kind) {
    const el = $('sld-cp-qr-status');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'sld-mqr-status' + (kind ? ' ' + kind : '');
  }

  function resetCreateQrState() {
    const input = $('sld-cp-qr');
    if (input) input.value = '';
    setCreateQrStatus('', '');
    createQrIsValid = false;
    createQrValidateSeq++;
    if (createQrValidateTimer) { clearTimeout(createQrValidateTimer); createQrValidateTimer = null; }
    const saveBtn = $('sld-cp-save');
    if (saveBtn) saveBtn.disabled = true;
  }

  function onCreateQrInput() {
    const input = $('sld-cp-qr');
    const saveBtn = $('sld-cp-save');
    if (!input || !saveBtn) return;
    const raw = input.value || '';
    createQrIsValid = false;
    saveBtn.disabled = true;
    if (!raw.trim()) {
      setCreateQrStatus('', '');
      if (createQrValidateTimer) { clearTimeout(createQrValidateTimer); createQrValidateTimer = null; }
      return;
    }
    setCreateQrStatus('Checking…', 'pending');
    if (createQrValidateTimer) clearTimeout(createQrValidateTimer);
    createQrValidateTimer = setTimeout(() => runCreateQrValidate(raw), 250);
  }

  async function runCreateQrValidate(raw) {
    const seq = ++createQrValidateSeq;
    const building = ($('sld-cp-building').value || currentBuilding || '').trim();
    if (!building) {
      setCreateQrStatus('No building selected', 'error');
      return;
    }
    try {
      const url = SLD_API + '/qr-codes/validate?qr=' + encodeURIComponent(raw)
                + '&building=' + encodeURIComponent(building);
      const resp = await fetch(url, { cache: 'no-store' });
      if (seq !== createQrValidateSeq) return;
      const data = await resp.json().catch(() => ({}));
      if (seq !== createQrValidateSeq) return;
      const saveBtn = $('sld-cp-save');
      if (data && data.valid) {
        setCreateQrStatus('OK — QR Exist In the Asset Capture Application', 'ok');
        createQrIsValid = true;
        if (saveBtn) saveBtn.disabled = false;
      } else {
        setCreateQrStatus((data && data.error) || 'QR Code not valid for this building', 'error');
        createQrIsValid = false;
        if (saveBtn) saveBtn.disabled = true;
      }
    } catch (e) {
      if (seq !== createQrValidateSeq) return;
      setCreateQrStatus('Network error: ' + (e && e.message ? e.message : e), 'error');
      createQrIsValid = false;
      const saveBtn = $('sld-cp-save');
      if (saveBtn) saveBtn.disabled = true;
    }
  }

  function updateAssetStats() {
    let matched = 0, ambiguous = 0;
    const hierarchies = new Set();
    (allAssets || []).forEach(a => {
      if (hasIdCheckMatch(a)) matched++;
      if (a.ambigous) ambiguous++;
      const h = a && a['Hierarchy'];
      if (h !== null && h !== undefined && String(h).trim() !== '') {
        hierarchies.add(String(h).trim());
      }
    });
    const setText = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    setText('sld-asset-count', (allAssets || []).length);
    setText('sld-stat-matched', matched);
    setText('sld-stat-unmatched', (missedAssets || []).length);
    setText('sld-stat-ambiguous', ambiguous);
    setText('sld-stat-hierarchy', hierarchies.size);
    setText('sld-missed-tab-count', (missedAssets || []).length);
    setText('sld-sdi-missing-tab-count', (sdiNotInSldAssets || []).length);
    const missedBtn = $('sld-view-missed-btn');
    if (missedBtn) missedBtn.classList.toggle('has-missed', (missedAssets || []).length > 0);
    const sdiMissingBtn = $('sld-view-sdi-missing-btn');
    if (sdiMissingBtn) sdiMissingBtn.classList.toggle('has-sdi-missing', (sdiNotInSldAssets || []).length > 0);
  }

  async function loadSdiNotInSldAssets(building) {
    try {
      const resp = await fetch(SLD_API + '/sdi-not-in-sld?building=' + encodeURIComponent(building || ''));
      if (!resp.ok) {
        const errorResult = await resp.json();
        showToast(errorResult.error || `Request failed (${resp.status})`, 'error');
        return;
      }
      const data = await resp.json();
      renderSdiNotInSldAssets(data || []);
    } catch (e) {
      showToast('Failed to load missing-from-SLD list: ' + e.message, 'error');
    }
  }

  function renderSdiNotInSldAssets(assets) {
    sdiNotInSldAssets = assets || [];
    const tbody = $('sld-sdi-missing-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!sdiNotInSldAssets.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="sld-missed-empty">No missing assets.</td></tr>';
      updateAssetStats();
      return;
    }

    sdiNotInSldAssets.forEach((asset, idx) => {
      const tag = asset['UBC Asset Tag'] || '';
      const qr = (asset['QR Code'] || '').toString().trim();
      const tr = document.createElement('tr');
      tr.innerHTML =
        `<td>${escapeHtml(tag)}</td>` +
        `<td>${escapeHtml(asset['Supply From'] || '')}</td>` +
        `<td>${escapeHtml(asset['Asset Group'] || '')}</td>` +
        `<td><span class="sld-sdi-qr">${escapeHtml(qr)}</span></td>` +
        `<td class="sld-action-col"><button type="button" class="ep-btn ep-btn-create sld-add-to-sld-btn" data-idx="${idx}">Add to SLD</button></td>`;
      tbody.appendChild(tr);
      const qrSpan = tr.querySelector('.sld-sdi-qr');
      if (qrSpan && qr && qr !== '—') {
        assetPhotoPopover.attach(qrSpan, qr);
        qrSpan.title = 'Hover to preview asset photo; click to pin';
      }
    });

    tbody.querySelectorAll('.sld-add-to-sld-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.idx);
        const row = sdiNotInSldAssets[idx];
        if (row) addSdiMissingAssetToSld(row, btn);
      });
    });

    updateAssetStats();
  }

  async function addSdiMissingAssetToSld(row, btn) {
    const tag = (row['UBC Asset Tag'] || '').trim();
    if (!tag) { showToast('UBC Asset Tag is required', 'error'); return; }
    if ([...allAssets, ...missedAssets].some(a => a["Equipment ID"] === tag)) {
      showToast(`Tag "${tag}" already exists in the SLD`, 'error');
      return;
    }

    const body = {
      "Equipment ID": tag,
      "QR Code": (row['QR Code'] || '').trim(),
      "Supply From": (row['Supply From'] || '').trim(),
      Building: (row['Building'] || currentBuilding || '').trim(),
      'Voltage Rating': (row['Voltage Rating'] || '').trim(),
      'Voltage Rating (UoM)': row['Voltage Rating (UoM)'] || 'V',
      'Amperage Rating': (row['Amperage Rating'] || '').trim(),
      'Amperage Rating (UoM)': row['Amperage Rating (UoM)'] || 'A',
      'Power Rating': (row['Power Rating'] || '').trim(),
      'Power Rating (UoM)': row['Power Rating (UoM)'] || '',
      Hierarchy: '1',
    };

    const previousText = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Adding...';
    }
    try {
      const resp = await fetch(SLD_API + '/assets', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      const result = await resp.json();
      if (!resp.ok) {
        showToast(result.error || 'Add to SLD failed', 'error');
        if (btn) {
          btn.disabled = false;
          btn.textContent = previousText || 'Add to SLD';
        }
        return;
      }

      renderDiagramOrEmpty(result.assets || []);
      renderMissedAssets(result.missed_assets || []);
      if (result.sdi_not_in_sld !== undefined) renderSdiNotInSldAssets(result.sdi_not_in_sld || []);
      loadBuildings();
      showToast(`Added ${tag} to SLD`, 'success');
    } catch (e) {
      showToast('Network error: ' + e.message, 'error');
      if (btn) {
        btn.disabled = false;
        btn.textContent = previousText || 'Add to SLD';
      }
    }
  }

  function openCreatePanelPrefilled(row) {
    openCreatePanel();
    const setVal = (id, v) => { const el = $(id); if (el) el.value = (v == null ? '' : String(v)); };

    setVal('sld-cp-tag', row['UBC Asset Tag'] || '');
    if (row['Building']) setVal('sld-cp-building', row['Building']);

    const parentSelect = $('sld-cp-parent');
    const parentVal = (row['Supply From'] || '').trim();
    if (parentSelect && parentVal) {
      const match = Array.from(parentSelect.options).find(o => o.value === parentVal);
      if (match) {
        parentSelect.value = parentVal;
      } else {
        parentSelect.value = '';
      }
    }

    setVal('sld-cp-voltage', row['Voltage Rating'] || '');
    if (row['Voltage Rating (UoM)']) setVal('sld-cp-voltage-uom', row['Voltage Rating (UoM)']);
    setVal('sld-cp-amperage', row['Amperage Rating'] || '');
    if (row['Amperage Rating (UoM)']) setVal('sld-cp-amperage-uom', row['Amperage Rating (UoM)']);
    setVal('sld-cp-power', row['Power Rating'] || '');
    if (row['Power Rating (UoM)']) setVal('sld-cp-power-uom', row['Power Rating (UoM)']);
  }

  function makeLinkPath(d) {
    const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y;
    if (orientation === 'horizontal') {
      const mx = (sx + tx) / 2;
      return `M${sx},${sy} L${mx},${sy} L${mx},${ty} L${tx},${ty}`;
    }
    const my = (sy + ty) / 2;
    return `M${sx},${sy} L${sx},${my} L${tx},${my} L${tx},${ty}`;
  }

  function hideEmptyState() { $('sld-empty-state').style.display = 'none'; }

  function buildTree(assets) {
    const nodeMap = new Map();
    assets.forEach(a => nodeMap.set(a["Equipment ID"], { ...a, children: [] }));

    const childOf = new Map();
    assets.forEach(a => {
      if (a["Supply From"] && nodeMap.has(a["Supply From"])) {
        childOf.set(a["Equipment ID"], a["Supply From"]);
      }
    });

    const cycleBreaks = new Set();
    const safe = new Set();
    childOf.forEach((_, tag) => {
      if (safe.has(tag)) return;
      const path = [];
      let cur = tag;
      const visited = new Set();
      while (cur && childOf.has(cur) && !safe.has(cur)) {
        if (visited.has(cur)) { cycleBreaks.add(cur); break; }
        visited.add(cur);
        path.push(cur);
        cur = childOf.get(cur);
      }
      path.forEach(p => safe.add(p));
    });
    cycleBreaks.forEach(tag => childOf.delete(tag));

    const roots = [];
    assets.forEach(a => {
      const node = nodeMap.get(a["Equipment ID"]);
      const parent = childOf.get(a["Equipment ID"]);
      if (parent) nodeMap.get(parent).children.push(node);
      else roots.push(node);
    });

    return { "Equipment ID": '__root__', children: roots, virtual: true };
  }

  function drawNodeShape(el, cfg, w, h) {
    if (cfg.shape === 'panel') {
      const bx = -w/2, by = -h/2, iconW = 30;
      el.append('rect').attr('x',bx).attr('y',by).attr('width',w).attr('height',h)
        .attr('rx',4).attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',1.5);
      el.append('rect').attr('x',bx+2).attr('y',by+2).attr('width',w-4).attr('height',h-4)
        .attr('rx',2).attr('fill','none').attr('stroke','#8899aa').attr('stroke-width',0.5);
      el.append('line').attr('x1',bx+iconW).attr('y1',by+4).attr('x2',bx+iconW).attr('y2',by+h-4)
        .attr('stroke','#8899aa').attr('stroke-width',0.5);
      const ix = bx + iconW/2;
      el.append('line').attr('x1',ix).attr('y1',by+5).attr('x2',ix).attr('y2',by+h-5)
        .attr('stroke','#fff').attr('stroke-width',1.2);
      el.append('rect').attr('x',ix-2.5).attr('y',by+7).attr('width',5).attr('height',7)
        .attr('rx',2.5).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('rect').attr('x',ix-2.5).attr('y',by+17).attr('width',5).attr('height',7)
        .attr('rx',2.5).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      const lx = ix, ly = by + h - 9;
      el.append('path')
        .attr('d',`M${lx+1},${ly} L${lx-2},${ly+3} L${lx},${ly+3} L${lx-1.5},${ly+6} L${lx+3},${ly+2.5} L${lx+1},${ly+2.5} Z`)
        .attr('fill','#fff').attr('stroke','none');
    } else if (cfg.shape === 'transformer') {
      const bx = -w/2, by = -h/2, iconW = 40;
      el.append('rect').attr('x',bx).attr('y',by).attr('width',w).attr('height',h)
        .attr('rx',3).attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',1.5);
      el.append('rect').attr('x',bx+2).attr('y',by+2).attr('width',iconW-2).attr('height',h-4)
        .attr('rx',2).attr('fill','rgba(255,255,255,0.1)');
      el.append('line').attr('x1',bx+iconW).attr('y1',by+2).attr('x2',bx+iconW).attr('y2',by+h-2)
        .attr('stroke','rgba(255,255,255,0.2)').attr('stroke-width',0.5);
      const cx = bx + iconW/2;
      [cx-10, cx, cx+10].forEach(bxp => {
        el.append('line').attr('x1',bxp).attr('y1',by-4).attr('x2',bxp).attr('y2',by+2)
          .attr('stroke',cfg.color).attr('stroke-width',2);
        for (let r = 0; r < 3; r++) {
          el.append('ellipse').attr('cx',bxp).attr('cy',by-4+2+r*3.5).attr('rx',3.5).attr('ry',1.8)
            .attr('fill','none').attr('stroke',cfg.color).attr('stroke-width',1.3);
        }
      });
      el.append('rect').attr('x',bx+4).attr('y',by+h).attr('width',iconW-6).attr('height',3)
        .attr('rx',1).attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',0.6);
      const boltX = cx - 8, boltTop = by + 8, boltBot = by + h - 8, boltMid = (boltTop+boltBot)/2;
      el.append('path')
        .attr('d', `M${boltX+2},${boltTop} L${boltX-2},${boltMid+1} L${boltX+1},${boltMid+1} L${boltX-1.5},${boltBot} L${boltX+4},${boltMid-1} L${boltX+1},${boltMid-1} Z`)
        .attr('fill','#fff').attr('stroke','none');
    } else if (cfg.shape === 'switchboard') {
      const bx = -w/2, by = -h/2, iconW = 34;
      el.append('rect').attr('x',bx).attr('y',by).attr('width',w).attr('height',h)
        .attr('rx',3).attr('fill',cfg.color).attr('stroke','#3a4550').attr('stroke-width',1.5);
      el.append('rect').attr('x',bx+2).attr('y',by+2).attr('width',iconW-4).attr('height',h-4)
        .attr('rx',1).attr('fill','#3a4550').attr('stroke','#2a3540').attr('stroke-width',0.6);
      for (let i = 0; i < 3; i++) {
        const gy = by + 5 + i * 3;
        el.append('line').attr('x1',bx+6).attr('y1',gy).attr('x2',bx+iconW-6).attr('y2',gy)
          .attr('stroke','#6b7c8a').attr('stroke-width',0.6);
      }
      el.append('rect').attr('x',bx+5).attr('y',by+h-14).attr('width',8).attr('height',4).attr('rx',0.5).attr('fill','#c0392b');
      el.append('rect').attr('x',bx+5).attr('y',by+h-9).attr('width',8).attr('height',4).attr('rx',0.5).attr('fill','#c0392b');
      el.append('line').attr('x1',bx+iconW).attr('y1',by+2).attr('x2',bx+iconW).attr('y2',by+h-2)
        .attr('stroke','#3a4550').attr('stroke-width',0.5);
      const rpX = bx + w - 22, cH = (h - 8) / 4;
      for (let i = 0; i < 4; i++) {
        const cy = by + 4 + i * cH;
        el.append('rect').attr('x',rpX).attr('y',cy).attr('width',18).attr('height',cH-2)
          .attr('rx',1).attr('fill','#64737e').attr('stroke','#3a4550').attr('stroke-width',0.4);
        const handleY = cy + cH/2 - 1;
        el.append('line').attr('x1',rpX+5).attr('y1',handleY-2).attr('x2',rpX+5).attr('y2',handleY+2)
          .attr('stroke','#c8d6e5').attr('stroke-width',2).attr('stroke-linecap','round');
        el.append('rect').attr('x',rpX+9).attr('y',cy+1).attr('width',7).attr('height',3).attr('rx',0.5).attr('fill','#2980b9');
      }
      const mbx = bx + iconW + 4, mby = by + h/2 - 4;
      el.append('rect').attr('x',mbx).attr('y',mby).attr('width',14).attr('height',8)
        .attr('rx',1).attr('fill','#3a4550').attr('stroke','#2a3540').attr('stroke-width',0.5);
      el.append('line').attr('x1',mbx+7).attr('y1',mby+2).attr('x2',mbx+14).attr('y2',mby+4)
        .attr('stroke','#c8d6e5').attr('stroke-width',1.5).attr('stroke-linecap','round');
      el.append('circle').attr('cx',mbx+7).attr('cy',by+10).attr('r',2).attr('fill','#c0392b').attr('stroke','#fff').attr('stroke-width',0.4);
    } else if (cfg.shape === 'ats') {
      const bx = -w/2, by = -h/2, iconW = 40;
      el.append('rect').attr('x',bx).attr('y',by).attr('width',w).attr('height',h)
        .attr('rx',3).attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',1.5);
      el.append('rect').attr('x',bx+2).attr('y',by+2).attr('width',iconW-2).attr('height',h-4)
        .attr('rx',2).attr('fill','rgba(0,0,0,0.15)');
      el.append('line').attr('x1',bx+iconW).attr('y1',by+2).attr('x2',bx+iconW).attr('y2',by+h-2)
        .attr('stroke','rgba(255,255,255,0.2)').attr('stroke-width',0.5);
      const cx = bx + iconW/2, cy = by + h/2;
      const inLeftX = cx - 10, inRightX = cx + 10, inTopY = by + 6, contactY = cy - 2;
      el.append('circle').attr('cx',inLeftX).attr('cy',inTopY).attr('r',2).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('circle').attr('cx',inRightX).attr('cy',inTopY).attr('r',2).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('line').attr('x1',inLeftX).attr('y1',inTopY+2).attr('x2',inLeftX).attr('y2',contactY).attr('stroke','#fff').attr('stroke-width',1.2);
      el.append('line').attr('x1',inRightX).attr('y1',inTopY+2).attr('x2',inRightX).attr('y2',contactY).attr('stroke','#fff').attr('stroke-width',1.2);
      el.append('circle').attr('cx',inLeftX).attr('cy',contactY).attr('r',1.8).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('circle').attr('cx',inRightX).attr('cy',contactY).attr('r',1.8).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      const outY = cy + 8;
      el.append('line').attr('x1',inLeftX).attr('y1',contactY).attr('x2',cx).attr('y2',outY-3).attr('stroke','#fff').attr('stroke-width',1.5);
      const outTermY = by + h - 6;
      el.append('line').attr('x1',cx).attr('y1',outY).attr('x2',cx).attr('y2',outTermY-2).attr('stroke','#fff').attr('stroke-width',1.2);
      el.append('circle').attr('cx',cx).attr('cy',outTermY).attr('r',2).attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
    } else if (cfg.shape === 'splitter') {
      const bx = -w/2, by = -h/2, iconW = 42;
      el.append('rect').attr('x',bx).attr('y',by).attr('width',w).attr('height',h)
        .attr('rx',3).attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',1.5);
      el.append('rect').attr('x',bx+2).attr('y',by+2).attr('width',iconW-2).attr('height',h-4)
        .attr('rx',2).attr('fill','rgba(255,255,255,0.15)');
      el.append('line').attr('x1',bx+iconW).attr('y1',by+2).attr('x2',bx+iconW).attr('y2',by+h-2)
        .attr('stroke','rgba(255,255,255,0.25)').attr('stroke-width',0.5);
      const cx = bx + iconW/2, topPlugY = by + 4;
      el.append('rect').attr('x',cx-5).attr('y',topPlugY).attr('width',10).attr('height',8).attr('rx',3).attr('ry',2)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',1.2);
      el.append('line').attr('x1',cx-2.5).attr('y1',topPlugY+3).attr('x2',cx+2.5).attr('y2',topPlugY+3).attr('stroke','#fff').attr('stroke-width',0.8);
      el.append('line').attr('x1',cx-2.5).attr('y1',topPlugY+5.5).attr('x2',cx+2.5).attr('y2',topPlugY+5.5).attr('stroke','#fff').attr('stroke-width',0.8);
      const stemMidY = topPlugY + 12;
      el.append('line').attr('x1',cx).attr('y1',topPlugY+8).attr('x2',cx).attr('y2',stemMidY).attr('stroke','#fff').attr('stroke-width',1.5);
      const barLeft = cx - 14, barRight = cx + 14;
      el.append('path').attr('d',`M${cx},${stemMidY} L${barLeft},${stemMidY} L${barLeft},${stemMidY+8}`)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',1.5).attr('stroke-linejoin','round');
      el.append('path').attr('d',`M${cx},${stemMidY} L${barRight},${stemMidY} L${barRight},${stemMidY+8}`)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',1.5).attr('stroke-linejoin','round');
      const botPlugY = stemMidY + 8;
      el.append('rect').attr('x',barLeft-4).attr('y',botPlugY).attr('width',8).attr('height',7).attr('rx',2).attr('ry',1.5)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('line').attr('x1',barLeft-2).attr('y1',botPlugY+2.5).attr('x2',barLeft+2).attr('y2',botPlugY+2.5).attr('stroke','#fff').attr('stroke-width',0.7);
      el.append('line').attr('x1',barLeft-2).attr('y1',botPlugY+4.5).attr('x2',barLeft+2).attr('y2',botPlugY+4.5).attr('stroke','#fff').attr('stroke-width',0.7);
      el.append('rect').attr('x',barRight-4).attr('y',botPlugY).attr('width',8).attr('height',7).attr('rx',2).attr('ry',1.5)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('line').attr('x1',barRight-2).attr('y1',botPlugY+2.5).attr('x2',barRight+2).attr('y2',botPlugY+2.5).attr('stroke','#fff').attr('stroke-width',0.7);
      el.append('line').attr('x1',barRight-2).attr('y1',botPlugY+4.5).attr('x2',barRight+2).attr('y2',botPlugY+4.5).attr('stroke','#fff').attr('stroke-width',0.7);
    } else if (cfg.shape === 'pnl') {
      const bx = -w/2, by = -h/2, iconW = 38;
      el.append('rect').attr('x',bx).attr('y',by).attr('width',w).attr('height',h)
        .attr('rx',3).attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',1.5);
      el.append('rect').attr('x',bx+3).attr('y',by+3).attr('width',iconW-4).attr('height',h-6)
        .attr('rx',2).attr('fill','rgba(255,255,255,0.2)').attr('stroke','rgba(255,255,255,0.4)').attr('stroke-width',0.6);
      el.append('line').attr('x1',bx+iconW).attr('y1',by+2).attr('x2',bx+iconW).attr('y2',by+h-2)
        .attr('stroke','rgba(255,255,255,0.25)').attr('stroke-width',0.5);
      el.append('rect').attr('x',bx+1).attr('y',by+10).attr('width',2.5).attr('height',5).attr('rx',0.5).attr('fill','rgba(255,255,255,0.5)');
      el.append('rect').attr('x',bx+1).attr('y',by+h-15).attr('width',2.5).attr('height',5).attr('rx',0.5).attr('fill','rgba(255,255,255,0.5)');
      const cx = bx + iconW/2;
      for (let i = 0; i < 4; i++) {
        el.append('circle').attr('cx',cx-9+i*6).attr('cy',by+8).attr('r',1.8).attr('fill','none').attr('stroke','#fff').attr('stroke-width',0.8);
      }
      const triTop = by + 14, triBot = by + 26, triHalf = 7;
      el.append('path').attr('d',`M${cx},${triTop} L${cx-triHalf},${triBot} L${cx+triHalf},${triBot} Z`)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',1);
      el.append('path').attr('d',`M${cx+0.5},${triTop+4} L${cx-1.5},${triBot-4} L${cx+0.5},${triBot-4} L${cx-0.5},${triBot-1.5}`)
        .attr('fill','none').attr('stroke','#fff').attr('stroke-width',0.8);
      for (let i = 0; i < 3; i++) {
        el.append('line').attr('x1',cx-10).attr('y1',by+30+i*3.5).attr('x2',cx+10).attr('y2',by+30+i*3.5).attr('stroke','#fff').attr('stroke-width',0.8);
      }
      for (let i = 0; i < 3; i++) {
        el.append('line').attr('x1',cx-3+i*3).attr('y1',by+h-2).attr('x2',cx-3+i*3).attr('y2',by+h+4)
          .attr('stroke',cfg.color).attr('stroke-width',1.2);
      }
    } else {
      el.append('rect').attr('x',-w/2).attr('y',-h/2).attr('width',w).attr('height',h).attr('rx',5)
        .attr('fill',cfg.color).attr('stroke','#c8d6e5').attr('stroke-width',1.5);
    }
  }

  function renderDiagramOrEmpty(assets) {
    if (!assets || assets.length === 0) {
      allAssets = [];
      showEmptyState();
      if (swiftActive) renderSwiftTable([]);
      return;
    }
    hideEmptyState();
    renderDiagram(assets);
    if (swiftActive) renderSwiftTable(allAssets || []);
  }

  function renderDiagram(assets) {
    allAssets = assets;
    updateAssetStats();
    // Close any photo popover anchored to a node we are about to remove.
    assetPhotoPopover.close();
    g.selectAll('*').remove();
    renderedNodeLookup = new Map();
    renderedNodesByEquipment = new Map();
    nodeSelection = null;
    linkSelection = null;
    ambiguousLinkSelection = null;
    findAreaLayer = null;

    const w = container.clientWidth, h = container.clientHeight;
    svgEl.attr('width', w).attr('height', h);

    const virtualRoot = buildTree(assets);
    ensureCollapseStateForBuilding(virtualRoot);
    const childrenAccessor = d => {
      if (d.virtual) return d.children;
      if (collapsedSet.has(d["Equipment ID"])) return null;
      return d.children;
    };
    const hierarchy = d3.hierarchy(virtualRoot, childrenAccessor);
    // d3.tree().nodeSize([dx, dy]) -- dx = sibling spacing, dy = depth spacing.
    // Each node draws its own ~50px box plus QR label (~17px above) and rating
    // label (~22px below), so the per-node visual span is ~90px. The previous
    // sibling spacing of 90 in the rotated (horizontal) layout left zero room
    // and the QR label of one sibling collided with the rating label of the
    // previous one. Bumped to 120 for the rotated case and to 180/160 for the
    // top-down case so labels keep clear of neighbors at every zoom level.
    const nodeSize = (orientation === 'horizontal') ? [120, 240] : [180, 160];
    const treeLayout = d3.tree().nodeSize(nodeSize).separation((a, b) => a.parent === b.parent ? 1 : 1.2);
    treeLayout(hierarchy);

    if (orientation === 'horizontal') {
      hierarchy.each(n => { const t = n.x; n.x = n.y; n.y = t; });
    }

    const nodes = hierarchy.descendants().filter(d => !d.data.virtual);
    const links = hierarchy.links().filter(d => !d.source.data.virtual);

    const linkGroup = g.append('g').attr('class', 'links');
    linkSelection = linkGroup.selectAll('path.link')
      .data(links.filter(d => !d.target.data.ambigous)).join('path').attr('class', 'link')
      .attr('data-source-row-id', d => getAssetRowId(d.source.data))
      .attr('data-target-row-id', d => getAssetRowId(d.target.data))
      .attr('d', makeLinkPath);
    ambiguousLinkSelection = linkGroup.selectAll('path.link-ambiguous')
      .data(links.filter(d => d.target.data.ambigous)).join('path').attr('class', 'link-ambiguous')
      .attr('data-source-row-id', d => getAssetRowId(d.source.data))
      .attr('data-target-row-id', d => getAssetRowId(d.target.data))
      .attr('d', makeLinkPath);

    const tooltip = d3.select('#sld-tooltip');
    findAreaLayer = g.append('g').attr('class', 'sld-find-area-layer');
    const nodeGroup = g.append('g').attr('class', 'nodes');
    nodeSelection = nodeGroup.selectAll('g.node').data(nodes).join('g')
      .attr('class', 'node')
      .attr('data-row-id', d => getAssetRowId(d.data))
      .attr('transform', d => `translate(${d.x},${d.y})`).style('cursor', 'pointer')
      .on('mouseenter', (event, d) => {
        const data = d.data;
        let html = `<div class="tt-tag">${escapeHtml(data["Equipment ID"])}</div>`;
        html += `<div class="tt-row"><span class="tt-key">Hierarchy</span><span>${escapeHtml(String(data.Hierarchy))}</span></div>`;
        if (data["Supply From"]) html += `<div class="tt-row"><span class="tt-key">Parent</span><span>${escapeHtml(data["Supply From"])}</span></div>`;
        const qrCode = getQrCodeText(data);
        if (qrCode) html += `<div class="tt-row"><span class="tt-key">QR Code</span><span>${escapeHtml(qrCode)}</span></div>`;
        if (data['Voltage Rating']) html += `<div class="tt-row"><span class="tt-key">Voltage</span><span>${escapeHtml(withRatingUnit(data['Voltage Rating'], 'V'))}</span></div>`;
        if (data['Amperage Rating']) html += `<div class="tt-row"><span class="tt-key">Amperage</span><span>${escapeHtml(withRatingUnit(data['Amperage Rating'], 'A'))}</span></div>`;
        if (data['Power Rating']) html += `<div class="tt-row"><span class="tt-key">Power</span><span>${escapeHtml(data['Power Rating'])} ${escapeHtml(data['Power Rating (UoM)'] || '')}</span></div>`;
        if (data.ambigous) html += `<div class="tt-row"><span class="tt-key" style="color:#e8913a">Ambiguous</span><span>${escapeHtml(data.ambigous)}</span></div>`;
        tooltip.html(html).style('opacity', 1);
      })
      .on('mousemove', (event) => { tooltip.style('left', (event.pageX+12)+'px').style('top', (event.pageY-10)+'px'); })
      .on('mouseleave', () => tooltip.style('opacity', 0))
      .on('click', (event, d) => { event.stopPropagation(); tooltip.style('opacity', 0); openEditPanel(d.data); });

    nodeSelection.each(function(d) {
      const el = d3.select(this);
      const cfg = getType(d.data["Equipment ID"]);
      const rowId = getAssetRowId(d.data);
      const parentRowId = d.parent && !d.parent.data.virtual ? getAssetRowId(d.parent.data) : '';
      const childRowIds = (d.children || [])
        .filter(child => child && child.data && !child.data.virtual)
        .map(child => getAssetRowId(child.data))
        .filter(Boolean);
      if (rowId) {
        renderedNodeLookup.set(rowId, {
          rowId,
          x: d.x,
          y: d.y,
          width: cfg.w,
          height: cfg.h,
          left: d.x - (cfg.w / 2),
          right: d.x + (cfg.w / 2),
          top: d.y - (cfg.h / 2),
          bottom: d.y + (cfg.h / 2),
          parentRowId,
          childRowIds,
        });
      }
      const equipmentKey = normalizeFindValue(d.data["Equipment ID"]);
      if (equipmentKey) {
        const siblings = renderedNodesByEquipment.get(equipmentKey) || [];
        siblings.push(rowId);
        renderedNodesByEquipment.set(equipmentKey, siblings);
      }
      drawNodeShape(el, cfg, cfg.w, cfg.h);
      if (d.data.ambigous) {
        const ambigRx = cfg.shape === 'panel' ? 3 : 5;
        el.append('rect').attr('x',-cfg.w/2).attr('y',-cfg.h/2).attr('width',cfg.w).attr('height',cfg.h).attr('rx',ambigRx)
          .attr('fill','none').attr('stroke','#e8913a').attr('stroke-width',2.5).attr('stroke-dasharray','4 2');
      }
      const matched = hasIdCheckMatch(d.data);
      const bSize = 14;
      const bx = cfg.w/2 - bSize/2 - 2;
      const by = -cfg.h/2 + bSize/2 + 2;
      const badge = el.append('g')
        .attr('class', matched ? 'sld-match ok' : 'sld-match bad')
        .attr('transform', `translate(${bx},${by})`);
      badge.append('circle').attr('r', bSize/2)
        .attr('fill', matched ? '#10b981' : '#ef4444')
        .attr('stroke', '#ffffff').attr('stroke-width', 1.5);
      badge.append('text').attr('text-anchor','middle').attr('dominant-baseline','central')
        .attr('fill','#ffffff').attr('font-size','10px').attr('font-weight','bold')
        .text(matched ? '\u2713' : '\u2717');
      badge.append('title').text(matched
        ? 'ID_check matches sdi_dataset_EL'
        : 'No ID_check match in sdi_dataset_EL');

      const dataObj = d.data;
      const equipmentId = dataObj["Equipment ID"];
      const hasChildren = (dataObj.children && dataObj.children.length > 0);
      if (hasChildren) {
        const isCollapsed = collapsedSet.has(equipmentId);
        const cbY = cfg.h / 2 + 28;
        const cbGroup = el.append('g')
          .attr('class', isCollapsed ? 'sld-collapse-badge' : 'sld-collapse-badge expanded')
          .attr('transform', `translate(0,${cbY})`)
          .on('click', (event) => {
            event.stopPropagation();
            toggleCollapseFor(equipmentId);
          });
        let label, titleText, pillW;
        if (isCollapsed) {
          const hiddenCount = countSubtreeNodes(dataObj);
          label = '▶ ' + hiddenCount;
          titleText = `Expand ${hiddenCount} hidden ` + (hiddenCount === 1 ? 'item' : 'items');
          pillW = Math.max(28, label.length * 7 + 4);
        } else {
          label = '▼';
          titleText = 'Collapse this subtree';
          pillW = 18;
        }
        const pillH = 14;
        cbGroup.append('rect')
          .attr('x', -pillW / 2)
          .attr('y', -pillH / 2)
          .attr('width', pillW)
          .attr('height', pillH)
          .attr('rx', 7);
        cbGroup.append('text')
          .attr('text-anchor', 'middle')
          .attr('dominant-baseline', 'central')
          .text(label);
        cbGroup.append('title').text(titleText);
      }

      el.append('rect')
        .attr('class', 'sld-find-node-frame')
        .attr('x', -cfg.w/2 - 8)
        .attr('y', -cfg.h/2 - 8)
        .attr('width', cfg.w + 16)
        .attr('height', cfg.h + 16)
        .attr('rx', cfg.shape === 'panel' ? 5 : 11);
    });

    const qrTextSel = nodeSelection.append('text').attr('class','node-qr-code')
      .attr('y', d => -getType(d.data["Equipment ID"]).h/2 - 7)
      .text(d => getQrCodeText(d.data));
    // Hover-and-pin asset photo popover anchored to the QR text.
    assetPhotoPopover.attachD3(qrTextSel, d => getQrCodeText(d.data));

    nodeSelection.append('text').attr('class','node-label')
      .attr('x', d => {
        const cfg = getType(d.data["Equipment ID"]);
        if (cfg.shape === 'panel') return 15;
        if (['switchboard','ats','splitter','pnl','transformer'].includes(cfg.shape)) return 5;
        return 0;
      })
      .attr('y', 1).text(d => d.data["Equipment ID"]);
    nodeSelection.append('text').attr('class','node-rating').attr('y', d => getType(d.data["Equipment ID"]).h/2+10).text(d => getRatingText(d.data));
    applyFindVisualState();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function renderEditPanelIcon(tag) {
    const svg = d3.select('#sld-ep-icon');
    svg.selectAll('*').remove();
    const cfg = getType(tag);
    const w = cfg.w, h = cfg.h;
    const svgW = 180, svgH = 56;
    const sc = Math.min(svgW / w, svgH / h) * 0.95;
    const grp = svg.append('g').attr('transform', `translate(${svgW/2},${svgH/2}) scale(${sc})`);
    drawNodeShape(grp, cfg, w, h);
    const labelX = (cfg.shape === 'panel') ? 15 : (['switchboard','ats','splitter','pnl','transformer'].includes(cfg.shape) ? 5 : 0);
    grp.append('text')
      .attr('x', labelX).attr('y', 1)
      .attr('text-anchor','middle').attr('dominant-baseline','central')
      .attr('fill','#fff').attr('font-size','11px').attr('font-weight','600').attr('font-family','Inter, system-ui, sans-serif')
      .text(tag);
  }

  function openEditPanel(data) {
    editingAsset = data;
    renderEditPanelIcon(data["Equipment ID"]);
    $('sld-ep-rowid').textContent = data.row_id;
    $('sld-ep-tag').value = data["Equipment ID"];
    $('sld-ep-hierarchy').textContent = 'Level ' + data.Hierarchy;
    $('sld-ep-voltage').textContent = data['Voltage Rating'] ? withRatingUnit(data['Voltage Rating'], 'V') : '-';
    const parts = [];
    if (data['Amperage Rating']) parts.push(withRatingUnit(data['Amperage Rating'], 'A'));
    if (data['Power Rating']) parts.push(data['Power Rating'] + ' ' + (data['Power Rating (UoM)'] || ''));
    $('sld-ep-rating').textContent = parts.join(' / ') || '-';

    const select = $('sld-ep-parent');
    select.innerHTML = '<option value="">(none — root level)</option>';
    allAssets.forEach(a => {
      if (a.row_id === data.row_id) return;
      const opt = document.createElement('option');
      opt.value = a["Equipment ID"]; opt.textContent = a["Equipment ID"];
      if (a["Equipment ID"] === data["Supply From"]) opt.selected = true;
      select.appendChild(opt);
    });

    const ambigSection = $('sld-ep-ambiguous-section');
    const ambigOptions = $('sld-ep-ambiguous-options');
    const parentSelect = $('sld-ep-parent');
    if (data.ambigous) {
      ambigSection.style.display = '';
      parentSelect.disabled = true;
      ambigOptions.innerHTML = '';
      data.ambigous.split('|').map(s => s.trim()).filter(Boolean).forEach((cand, i) => {
        const isCurrent = cand === data["Supply From"];
        const div = document.createElement('div');
        div.className = 'ep-ambig-option' + (isCurrent ? ' selected' : '');
        div.innerHTML = `<input type="radio" name="sld-ambig-parent" value="${escapeHtml(cand)}" id="sld-ambig-${i}" ${isCurrent?'checked':''}>` +
          `<label for="sld-ambig-${i}" class="ambig-tag">${escapeHtml(cand)}</label>` + (isCurrent ? '<span class="ambig-current">current parent</span>' : '');
        div.addEventListener('click', () => {
          div.querySelector('input').checked = true;
          ambigOptions.querySelectorAll('.ep-ambig-option').forEach(el => el.classList.remove('selected'));
          div.classList.add('selected');
        });
        ambigOptions.appendChild(div);
      });
    } else { ambigSection.style.display = 'none'; parentSelect.disabled = false; }
    $('sld-edit-overlay').classList.add('open');
    $('sld-ep-tag').focus();
  }

  function closeEditPanel() { $('sld-edit-overlay').classList.remove('open'); editingAsset = null; }

  async function saveAsset() {
    if (!editingAsset) return;
    const newTag = $('sld-ep-tag').value.trim();
    const newParent = $('sld-ep-parent').value;
    if (!newTag) { showToast('Equipment ID cannot be empty', 'error'); return; }
    const btn = $('sld-ep-save'); btn.disabled = true; btn.textContent = 'Saving...';
    try {
      const body = {};
      if (newTag !== editingAsset["Equipment ID"]) body["Equipment ID"] = newTag;
      if (editingAsset.ambigous) {
        const chosen = document.querySelector('input[name="sld-ambig-parent"]:checked');
        if (chosen) { body["Supply From"] = chosen.value; body.ambigous = ''; }
      } else {
        if (newParent !== (editingAsset["Supply From"] || '')) body["Supply From"] = newParent;
      }
      if (Object.keys(body).length === 0) { closeEditPanel(); return; }
      const resp = await fetch(SLD_API + '/assets/' + editingAsset.row_id + '?building=' + encodeURIComponent(currentBuilding || ''),
        { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const result = await resp.json();
      if (resp.ok) {
        closeEditPanel();
        renderDiagramOrEmpty(result.assets || []);
        renderMissedAssets(result.missed_assets || []);
      if (result.sdi_not_in_sld !== undefined) renderSdiNotInSldAssets(result.sdi_not_in_sld || []);
        if (!focusActiveSearchIfPossible()) fitToScreen();
        showToast('Saved - diagram updated', 'success');
        return;
      }
      showToast(result.error || 'Save failed', 'error');
    } catch (e) { showToast('Network error: ' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = 'Save Changes'; }
  }

  async function deleteAsset() {
    if (!editingAsset) return;
    const tag = editingAsset["Equipment ID"];
    const children = allAssets.filter(a => a["Supply From"] === tag);
    let msg = `Delete "${tag}"?`;
    if (children.length > 0) {
      msg += `\n\nThis asset has ${children.length} child(ren) that will be re-parented to "${editingAsset["Supply From"] || '(root)'}":\n` +
        children.map(c => '  - ' + c["Equipment ID"]).join('\n');
    }
    if (!confirm(msg)) return;
    const btn = $('sld-ep-delete'); btn.disabled = true; btn.textContent = 'Deleting...';
    try {
      const resp = await fetch(SLD_API + '/assets/' + editingAsset.row_id + '?building=' + encodeURIComponent(currentBuilding || ''),
        { method: 'DELETE' });
      const result = await resp.json();
      if (resp.ok) {
        closeEditPanel();
        renderDiagramOrEmpty(result.assets || []);
        renderMissedAssets(result.missed_assets || []);
      if (result.sdi_not_in_sld !== undefined) renderSdiNotInSldAssets(result.sdi_not_in_sld || []);
        if (!focusActiveSearchIfPossible()) fitToScreen();
        showToast(`Deleted ${tag}`, 'success');
        return;
      }
      showToast(result.error || 'Delete failed', 'error');
    } catch (e) { showToast('Network error: ' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = 'Delete'; }
  }

  function openCreatePanel() {
    $('sld-cp-tag').value = '';
    $('sld-cp-building').value = currentBuilding || '314-1';
    $('sld-cp-voltage').value = ''; $('sld-cp-voltage-uom').value = 'V';
    $('sld-cp-amperage').value = ''; $('sld-cp-amperage-uom').value = 'A';
    $('sld-cp-power').value = ''; $('sld-cp-power-uom').value = '';
    const select = $('sld-cp-parent');
    select.innerHTML = '<option value="">(none — root level)</option>';
    allAssets.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a["Equipment ID"]; opt.textContent = a["Equipment ID"]; select.appendChild(opt);
    });
    resetCreateQrState();
    $('sld-create-overlay').classList.add('open');
    $('sld-cp-qr').focus();
  }

  function closeCreatePanel() { $('sld-create-overlay').classList.remove('open'); }

  async function createAsset() {
    const tag = $('sld-cp-tag').value.trim();
    const qrCode = ($('sld-cp-qr').value || '').trim();
    if (!qrCode || !createQrIsValid) { showToast('A valid QR Code is required', 'error'); return; }
    if (!tag) { showToast('Equipment ID is required', 'error'); return; }
    if ([...allAssets, ...missedAssets].some(a => a["Equipment ID"] === tag)) { showToast(`Tag "${tag}" already exists`, 'error'); return; }
    const btn = $('sld-cp-save'); btn.disabled = true; btn.textContent = 'Creating...';
    const body = {
      "Equipment ID": tag,
      "QR Code": qrCode,
      "Supply From": $('sld-cp-parent').value,
      Building: $('sld-cp-building').value.trim() || '314-1',
      'Voltage Rating': $('sld-cp-voltage').value.trim(),
      'Voltage Rating (UoM)': $('sld-cp-voltage-uom').value,
      'Amperage Rating': $('sld-cp-amperage').value.trim(),
      'Amperage Rating (UoM)': $('sld-cp-amperage-uom').value,
      'Power Rating': $('sld-cp-power').value.trim(),
      'Power Rating (UoM)': $('sld-cp-power-uom').value,
      Hierarchy: '1',
    };
    try {
      const resp = await fetch(SLD_API + '/assets', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const result = await resp.json();
      if (resp.ok) {
        closeCreatePanel();
        renderDiagramOrEmpty(result.assets || []);
        renderMissedAssets(result.missed_assets || []);
      if (result.sdi_not_in_sld !== undefined) renderSdiNotInSldAssets(result.sdi_not_in_sld || []);
        if (!focusActiveSearchIfPossible()) fitToScreen();
        showToast(`Created ${tag}`, 'success');
        loadBuildings();
        return;
      }
      showToast(result.error || 'Create failed', 'error');
    } catch (e) { showToast('Network error: ' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = 'Create Asset'; }
  }

  function openImportPanel() {
    clearSelectedFile();
    $('sld-import-conflict-box').style.display = 'none';
    $('sld-import-conflict-box').innerHTML = '';
    $('sld-import-progress').style.display = 'none';
    refreshImportBuildingInfo();
    updateImportButtonState();
    uploadResult = null;
    $('sld-import-overlay').classList.add('open');
  }

  function closeImportPanel() {
    $('sld-import-overlay').classList.remove('open');
    stopStatusBar();
    clearSelectedFile();
  }

  function clearSelectedFile() {
    selectedFile = null; uploadResult = null;
    resetPdfPreview();
    $('sld-file-input').value = '';
    $('sld-file-selected').style.display = 'none';
    $('sld-drop-zone').style.display = '';
    refreshImportBuildingInfo();
    $('sld-import-progress').style.display = 'none';
    $('sld-import-progress').innerHTML = '';
    $('sld-import-btn').textContent = 'Upload & Process';
    updateImportButtonState();
  }

  function getImportBuildingCode() {
    const sldSel = $('sld-building-selector');
    const ms = window.globalBuildingMs;
    const msCode = (ms && typeof ms.values === 'function' && ms.values().length === 1)
      ? ms.values()[0] : '';
    const globalSel = document.getElementById('global-building-selector');
    return String(
      currentBuilding ||
      (sldSel && sldSel.value) ||
      msCode ||
      (globalSel && globalSel.value) ||
      ''
    ).trim();
  }

  function getImportBuildingDisplay(building) {
    const match = (buildingsData || []).find(x => x.building === building);
    if (match && match.display) return match.display;
    const ms = window.globalBuildingMs;
    if (ms && typeof ms.values === 'function') {
      const vals = ms.values();
      if (vals.length === 1 && vals[0] === building) {
        const disp = getGlobalSelectedDisplay();
        if (disp) return disp;
      }
    }
    const globalSel = document.getElementById('global-building-selector');
    if (globalSel && globalSel.value === building && globalSel.selectedOptions && globalSel.selectedOptions[0]) {
      const text = (globalSel.selectedOptions[0].textContent || '').trim();
      if (text) return text;
    }
    return building || '';
  }

  function formatImportBuildingLabel(display, building) {
    if (!building) return '';
    return display && display !== building ? `${display} (${building})` : building;
  }

  function refreshImportBuildingInfo() {
    const building = getImportBuildingCode();
    const display = getImportBuildingDisplay(building);
    const info = $('sld-import-building-code');
    const conflictBox = $('sld-import-conflict-box');

    if (info) info.textContent = building ? formatImportBuildingLabel(display, building) : 'Select a building before importing';

    if (!conflictBox) return;
    const match = (buildingsData || []).find(x => x.building === building);
    if (building && match && Number(match.current_count || 0) > 0) {
      conflictBox.style.display = 'block';
      conflictBox.innerHTML =
        `<div class="import-conflict">` +
        `<strong>${escapeHtml(display || building)} already has an active drawing.</strong><br>` +
        `Uploading this PDF will archive the current drawing and create a new one.` +
        `</div>`;
    } else {
      conflictBox.style.display = 'none';
      conflictBox.innerHTML = '';
    }
  }

  function updateImportButtonState() {
    const btn = $('sld-import-btn');
    if (!btn) return;
    btn.disabled = !!importBusy || !selectedFile || !getImportBuildingCode() || !pdfPreviewReady;
  }

  async function onFileSelected(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) { showToast('File must be a PDF (.pdf)', 'error'); return; }

    selectedFile = file;
    $('sld-drop-zone').style.display = 'none';
    $('sld-file-selected').style.display = 'flex';
    $('sld-file-selected-name').textContent = file.name;
    refreshImportBuildingInfo();
    updateImportButtonState();
    await loadPdfPreview(file);
  }

  function setPdfPreviewStatus(message) {
    const el = $('sld-pdf-status');
    if (el) el.textContent = message || '';
  }

  function resetPdfPreview() {
    pdfPreviewToken += 1;
    pdfPreviewReady = false;
    if (pdfPreviewRenderTask) {
      try { pdfPreviewRenderTask.cancel(); } catch (_e) { /* noop */ }
      pdfPreviewRenderTask = null;
    }
    if (pdfPreviewDoc && typeof pdfPreviewDoc.destroy === 'function') {
      try { pdfPreviewDoc.destroy(); } catch (_e) { /* noop */ }
    }
    pdfPreviewDoc = null;
    pdfPreviewPage = 1;
    pdfPreviewScale = 1;
    pdfPreviewRotation = 0;
    const preview = $('sld-pdf-preview');
    if (preview) preview.hidden = true;
    const canvas = $('sld-pdf-canvas');
    if (canvas) {
      const ctx = canvas.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
      canvas.width = 0;
      canvas.height = 0;
    }
    setPdfPreviewStatus('');
    updatePdfPreviewControls();
  }

  function updatePdfPreviewControls() {
    const pageCount = pdfPreviewDoc ? pdfPreviewDoc.numPages : 1;
    const pageLabel = $('sld-pdf-page-label');
    const zoomLabel = $('sld-pdf-zoom-label');
    if (pageLabel) pageLabel.textContent = `Page ${pdfPreviewPage} / ${pageCount || 1}`;
    if (zoomLabel) zoomLabel.textContent = `${Math.round(pdfPreviewScale * 100)}%`;
    const prev = $('sld-pdf-prev-btn');
    const next = $('sld-pdf-next-btn');
    const zoomOut = $('sld-pdf-zoom-out-btn');
    const zoomIn = $('sld-pdf-zoom-in-btn');
    const rotate = $('sld-pdf-rotate-btn');
    if (prev) prev.disabled = !pdfPreviewDoc || pdfPreviewPage <= 1;
    if (next) next.disabled = !pdfPreviewDoc || pdfPreviewPage >= pageCount;
    if (zoomOut) zoomOut.disabled = !pdfPreviewDoc || pdfPreviewScale <= PDF_PREVIEW_MIN_SCALE;
    if (zoomIn) zoomIn.disabled = !pdfPreviewDoc || pdfPreviewScale >= PDF_PREVIEW_MAX_SCALE;
    if (rotate) rotate.disabled = !pdfPreviewDoc;
  }

  function configurePdfJsWorker() {
    if (!window.pdfjsLib) return false;
    try {
      if (!window.pdfjsLib.GlobalWorkerOptions.workerSrc) {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc =
          'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
      }
    } catch (_e) { /* noop */ }
    return true;
  }

  function bindPdfPreviewPan() {
    const stage = document.querySelector('#sld-pdf-preview .sld-pdf-stage');
    if (!stage) return;

    let activePointerId = null;
    let startX = 0;
    let startY = 0;
    let startScrollLeft = 0;
    let startScrollTop = 0;

    function stopDrag() {
      activePointerId = null;
      stage.classList.remove('is-dragging');
    }

    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || !pdfPreviewDoc) return;
      const canPan = stage.scrollWidth > stage.clientWidth || stage.scrollHeight > stage.clientHeight;
      if (!canPan) return;
      activePointerId = e.pointerId;
      startX = e.clientX;
      startY = e.clientY;
      startScrollLeft = stage.scrollLeft;
      startScrollTop = stage.scrollTop;
      stage.classList.add('is-dragging');
      if (stage.setPointerCapture) stage.setPointerCapture(e.pointerId);
      e.preventDefault();
    });

    stage.addEventListener('pointermove', (e) => {
      if (activePointerId !== e.pointerId) return;
      stage.scrollLeft = startScrollLeft - (e.clientX - startX);
      stage.scrollTop = startScrollTop - (e.clientY - startY);
      e.preventDefault();
    });

    stage.addEventListener('pointerup', stopDrag);
    stage.addEventListener('pointercancel', stopDrag);
    stage.addEventListener('lostpointercapture', stopDrag);
  }

  async function loadPdfPreview(file) {
    resetPdfPreview();
    const token = pdfPreviewToken;
    const preview = $('sld-pdf-preview');
    if (preview) preview.hidden = false;
    setPdfPreviewStatus('Loading PDF...');
    updateImportButtonState();

    if (!configurePdfJsWorker()) {
      setPdfPreviewStatus('PDF preview library failed to load. Refresh the page.');
      showToast('PDF preview library failed to load. Refresh the page.', 'error');
      return;
    }

    try {
      const data = await file.arrayBuffer();
      if (token !== pdfPreviewToken) return;
      pdfPreviewDoc = await window.pdfjsLib.getDocument({ data }).promise;
      if (token !== pdfPreviewToken) return;
      pdfPreviewPage = 1;
      pdfPreviewRotation = 0;
      await fitPdfPreviewToStage(token);
      await renderPdfPreviewPage(token);
    } catch (e) {
      if (token !== pdfPreviewToken) return;
      pdfPreviewDoc = null;
      pdfPreviewReady = false;
      setPdfPreviewStatus('Unable to preview this PDF.');
      showToast('Unable to preview this PDF: ' + (e && e.message ? e.message : 'unknown error'), 'error');
      updatePdfPreviewControls();
      updateImportButtonState();
    }
  }

  function getPdfPreviewStageBounds() {
    const stage = document.querySelector('#sld-pdf-preview .sld-pdf-stage');
    if (!stage) return null;
    const style = window.getComputedStyle(stage);
    const horizontalPadding = parseFloat(style.paddingLeft || '0') + parseFloat(style.paddingRight || '0');
    const verticalPadding = parseFloat(style.paddingTop || '0') + parseFloat(style.paddingBottom || '0');
    return {
      width: Math.max(stage.clientWidth - horizontalPadding, 160),
      height: Math.max(stage.clientHeight - verticalPadding, 160)
    };
  }

  async function fitPdfPreviewToStage(token) {
    if (!pdfPreviewDoc) return;
    const page = await pdfPreviewDoc.getPage(pdfPreviewPage);
    if (token !== pdfPreviewToken) return;
    const viewport = page.getViewport({ scale: 1, rotation: pdfPreviewRotation });
    const bounds = getPdfPreviewStageBounds();
    if (!bounds || !viewport.width || !viewport.height) {
      pdfPreviewScale = 1;
      return;
    }
    const fitScale = Math.min(bounds.width / viewport.width, bounds.height / viewport.height);
    pdfPreviewScale = Math.min(Math.max(fitScale, PDF_PREVIEW_MIN_SCALE), PDF_PREVIEW_MAX_SCALE);
  }

  async function renderPdfPreviewPage(token) {
    if (!pdfPreviewDoc) return;
    pdfPreviewReady = false;
    updateImportButtonState();
    setPdfPreviewStatus('Rendering page...');
    updatePdfPreviewControls();
    let renderTask = null;
    try {
      const page = await pdfPreviewDoc.getPage(pdfPreviewPage);
      if (token !== pdfPreviewToken) return;
      const viewport = page.getViewport({ scale: pdfPreviewScale, rotation: pdfPreviewRotation });
      const canvas = $('sld-pdf-canvas');
      const ctx = canvas.getContext('2d');
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      if (pdfPreviewRenderTask) {
        try { pdfPreviewRenderTask.cancel(); } catch (_e) { /* noop */ }
      }
      renderTask = page.render({ canvasContext: ctx, viewport });
      pdfPreviewRenderTask = renderTask;
      await renderTask.promise;
      if (token !== pdfPreviewToken) return;
      if (pdfPreviewRenderTask === renderTask) pdfPreviewRenderTask = null;
      pdfPreviewReady = true;
      setPdfPreviewStatus('');
    } catch (e) {
      if (e && e.name === 'RenderingCancelledException') return;
      if (token !== pdfPreviewToken) return;
      pdfPreviewReady = false;
      setPdfPreviewStatus('Unable to render this page.');
      showToast('Unable to render PDF page: ' + (e && e.message ? e.message : 'unknown error'), 'error');
    } finally {
      if (renderTask && pdfPreviewRenderTask === renderTask) {
        pdfPreviewRenderTask = null;
      }
      updatePdfPreviewControls();
      updateImportButtonState();
    }
  }

  function changePdfPreviewPage(delta) {
    if (!pdfPreviewDoc) return;
    const nextPage = Math.min(Math.max(pdfPreviewPage + delta, 1), pdfPreviewDoc.numPages);
    if (nextPage === pdfPreviewPage) return;
    pdfPreviewPage = nextPage;
    renderPdfPreviewPage(pdfPreviewToken);
  }

  function zoomPdfPreview(factor) {
    if (!pdfPreviewDoc) return;
    const nextScale = Math.min(Math.max(pdfPreviewScale * factor, PDF_PREVIEW_MIN_SCALE), PDF_PREVIEW_MAX_SCALE);
    if (Math.abs(nextScale - pdfPreviewScale) < 0.01) return;
    pdfPreviewScale = nextScale;
    renderPdfPreviewPage(pdfPreviewToken);
  }

  async function rotatePdfPreview() {
    if (!pdfPreviewDoc) return;
    const token = pdfPreviewToken;
    pdfPreviewRotation = (pdfPreviewRotation + 90) % 360;
    try { await fitPdfPreviewToStage(token); } catch (_e) { /* keep the current scale */ }
    if (token === pdfPreviewToken) renderPdfPreviewPage(token);
  }

  function startStatusBar(steps) {
    const progress = $('sld-import-progress');
    progress.style.display = 'block';
    progress.classList.add('import-progress');
    const startTime = Date.now();
    const stepsHtml = steps.map((s, i) =>
      `<li id="sld-status-step-${i}" class="${i === 0 ? 'active' : ''}"><span class="step-icon">${i === 0 ? '<span class="spinner"></span>' : '&#9675;'}</span> ${s}</li>`
    ).join('');
    progress.innerHTML =
      `<div class="status-bar">` +
        `<div class="status-bar-header">` +
          `<span class="status-bar-label"><span class="spinner"></span> Processing...</span>` +
          `<span class="status-bar-timer" id="sld-status-timer">00:00</span>` +
        `</div>` +
        `<div class="status-bar-track"><div class="status-bar-fill"></div></div>` +
        `<ul class="status-bar-steps">${stepsHtml}</ul>` +
      `</div>`;
    if (statusTimer) clearInterval(statusTimer);
    statusTimer = setInterval(() => {
      const el = $('sld-status-timer');
      if (!el) { clearInterval(statusTimer); return; }
      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const s = String(elapsed % 60).padStart(2, '0');
      el.textContent = `${m}:${s}`;
    }, 1000);
  }

  function advanceStep(index) {
    for (let i = 0; i < index; i++) {
      const el = $(`sld-status-step-${i}`);
      if (el) { el.className = 'done'; el.querySelector('.step-icon').innerHTML = '&#10003;'; }
    }
    const cur = $(`sld-status-step-${index}`);
    if (cur) { cur.className = 'active'; cur.querySelector('.step-icon').innerHTML = '<span class="spinner"></span>'; }
  }

  function stopStatusBar() {
    if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
  }

  async function startImport() {
    const buildingCode = getImportBuildingCode();
    if (!selectedFile) { showToast('Select a PDF before processing.', 'error'); return; }
    if (!buildingCode) { showToast('Select a building before importing.', 'error'); return; }
    if (!pdfPreviewReady) { showToast('Wait for the PDF preview to load before processing.', 'error'); return; }
    const btn = $('sld-import-btn');
    const progress = $('sld-import-progress');

    importBusy = true;
    updateImportButtonState();
    btn.textContent = 'Uploading...';
    startStatusBar(['Uploading PDF...', 'Confirming building...', 'Extracting schema from PDF...', 'Building diagram...']);

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('building_code', buildingCode);

      const upResp = await fetch(SLD_API + '/upload', { method: 'POST', body: formData });
      const upResult = await upResp.json();

      if (!upResp.ok) {
        showToast(upResult.error || 'Upload failed', 'error');
        stopStatusBar(); progress.style.display = 'none'; btn.textContent = 'Upload & Process';
        return;
      }

      uploadResult = upResult;
      $('sld-import-building-code').textContent =
        formatImportBuildingLabel(upResult.building_display || upResult.building_code, upResult.building_code);
      advanceStep(1);

      advanceStep(2);
      await processExtraction(upResult.building_code, upResult.filename, true);
    } catch (e) {
      showToast('Network error: ' + e.message, 'error');
      stopStatusBar(); progress.style.display = 'none';
    } finally {
      importBusy = false;
      btn.textContent = 'Upload & Process'; btn.style.display = '';
      updateImportButtonState();
    }
  }

  async function processExtraction(buildingCode, filename, replace) {
    const btn = $('sld-import-btn');
    const progress = $('sld-import-progress');
    btn.textContent = 'Processing...';
    updateImportButtonState();
    advanceStep(2);

    try {
      if (!filename || !buildingCode) {
        showToast('Missing upload filename or building code.', 'error');
        return;
      }
      const processPayload = {
        filename: filename,
        building_code: buildingCode,
        replace: Boolean(replace)
      };
      const processUrl = SLD_API + '/process';
      const resp = await fetch(processUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(processPayload)
      });
      const contentType = resp.headers.get('content-type') || '';
      const result = contentType.indexOf('application/json') !== -1
        ? await resp.json()
        : { error: await resp.text() };

      if (resp.ok) {
        advanceStep(3);
        stopStatusBar();
        closeImportPanel();
        currentBuilding = buildingCode;
        await loadBuildings();
        $('sld-building-selector').value = buildingCode;
        showToast(`Imported ${result.assets.length} assets for Building ${buildingCode}`, 'success');
      } else {
        stopStatusBar();
        progress.innerHTML = '';
        const errMsg = result.error || 'Processing failed';
        showToast(errMsg, 'error');
        if (result.stderr) {
          progress.style.display = 'block';
          progress.innerHTML = `<div style="text-align:left;font-size:11px;color:#e74c3c;white-space:pre-wrap;max-height:150px;overflow-y:auto;padding:14px">${escapeHtml(result.stderr)}</div>`;
        }
      }
    } catch (e) {
      stopStatusBar();
      console.error('[sld] /process fetch rejected:', e);
      showToast('Network error: ' + (e && e.message ? e.message : e), 'error');
    } finally {
      btn.textContent = 'Upload & Process'; btn.style.display = '';
      updateImportButtonState();
    }
  }

  // ── Swift Over (inline editable table) ─────────────────────────────────

  let swiftActive = false;

  async function ensureSwiftRoomLocations(building) {
    if (!building) {
      swiftRoomLocations = [];
      swiftRoomLocationsBuilding = null;
      renderSwiftRoomDatalist();
      return;
    }
    if (building === swiftRoomLocationsBuilding) return;
    try {
      const resp = await fetch(
        SLD_API + '/locations?building=' + encodeURIComponent(building),
        { cache: 'no-store' }
      );
      swiftRoomLocations = resp.ok ? (await resp.json() || []) : [];
    } catch (_e) {
      swiftRoomLocations = [];
    }
    swiftRoomLocationsBuilding = building;
    renderSwiftRoomDatalist();
  }

  function renderSwiftRoomDatalist() {
    const dl = document.getElementById('sld-swift-room-locations');
    if (!dl) return;
    dl.innerHTML = swiftRoomLocations
      .map((v) => `<option value="${escapeHtml(v)}"></option>`)
      .join('');
  }

  async function openSwiftOver() {
    swiftActive = true;
    const swiftPane = $('sld-swift-over-pane');
    const diagramView = $('sld-diagram-view');
    const toggle = $('sld-swift-over-toggle');

    if (diagramView) { diagramView.classList.remove('active'); diagramView.setAttribute('aria-hidden','true'); }
    if (swiftPane) {
      swiftPane.style.display = 'block';
      swiftPane.setAttribute('aria-hidden', 'false');
    }
    if (toggle) toggle.checked = true;

    switchSwiftView('swift-table');

    renderSwiftTable(allAssets || []);
    if (!currentBuilding) return;
    // Refresh the Room dropdown options in parallel with the asset fetch so the
    // datalist is populated before buildSwiftRow runs on the fresh rows.
    const locationsPromise = ensureSwiftRoomLocations(currentBuilding);
    try {
      const resp = await fetch(
        SLD_API + '/assets?building=' + encodeURIComponent(currentBuilding) + '&_=' + Date.now(),
        { cache: 'no-store' }
      );
      if (!resp.ok) {
        const errorResult = await resp.json();
        showToast(errorResult.error || `Request failed (${resp.status})`, 'error');
        return;
      }
      allAssets = await resp.json() || [];
      await locationsPromise;
      renderSwiftTable(allAssets);
      updateAssetStats();
    } catch (e) {
      showToast('Failed to refresh Swift Over data: ' + e.message, 'error');
    }
  }

  function closeSwiftOver(options) {
    swiftActive = false;
    const swiftPane = $('sld-swift-over-pane');
    const toggle = $('sld-swift-over-toggle');

    if (swiftPane) {
      swiftPane.style.display = 'none';
      swiftPane.setAttribute('aria-hidden', 'true');
    }
    if (toggle) toggle.checked = false;
    switchSldView('diagram', options);
  }

  function renderSwiftTable(assets) {
    const tbody = $('sld-swift-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!assets || !assets.length) {
      tbody.innerHTML = '<tr><td colspan="12" class="sld-swift-empty">No assets in this building.</td></tr>';
      applySwiftColumnWidths([]);
      syncSwiftFindHighlight();
      return;
    }
    const equipmentQrLookup = buildEquipmentQrLookup(assets);
    assets.forEach((asset) => {
      tbody.appendChild(buildSwiftRow(asset, equipmentQrLookup));
    });
    tagSwiftHierarchyGroups();
    applySwiftColumnWidths(assets);
    syncSwiftFindHighlight({ scrollIntoView: !!activeFindResultId && swiftActive });
  }

  // Walk the rendered rows in order and mark each row that opens or closes a
  // contiguous run of equal Hierarchy values. CSS uses these classes to draw a
  // subtle inset shadow around the group, so visually similar hierarchies read
  // as a single band without altering the matched/unmatched per-row coloring.
  function tagSwiftHierarchyGroups() {
    const rows = document.querySelectorAll('#sld-swift-tbody .sld-swift-row');
    let prev = null;
    rows.forEach((row) => {
      row.classList.remove('is-hierarchy-group-start', 'is-hierarchy-group-end');
      const h = row.dataset.hierarchy || '';
      if (prev === null || (prev.dataset.hierarchy || '') !== h) {
        row.classList.add('is-hierarchy-group-start');
        if (prev) prev.classList.add('is-hierarchy-group-end');
      }
      prev = row;
    });
    if (prev) prev.classList.add('is-hierarchy-group-end');
  }

  // Size each column to fit the widest value in that column (plus padding)
  // using the `ch` unit. `ch` ≈ width of the "0" glyph in the current font —
  // close enough for monospace-leaning inputs and stable across zooms.
  function applySwiftColumnWidths(assets) {
    const colgroup = document.getElementById('sld-swift-colgroup');
    if (!colgroup) return;
    const cols = colgroup.querySelectorAll('col');
    if (cols.length !== 12) return;
    const equipmentQrLookup = buildEquipmentQrLookup(assets || []);

    // [label used for header length, min-chars floor, max-chars cap, extractor]
    // Check + Save stay fixed (icon-sized), so they get `null` extractors.
    // Caps are intentionally generous so each column truly fits the widest
    // value in the data instead of being clipped by an arbitrary limit.
    const specs = [
      { header: 'Hierarchy',    min: 8,  max: 80, get: a => String(a['Hierarchy'] != null ? a['Hierarchy'] : '') },
      { header: 'Equip QR Code', min: 13, max: 80, get: a => getQrCodeText(a) || '' },
      { header: 'Equipment ID', min: 10, max: 80, get: a => a['Equipment ID'] || '' },
      { header: 'Fed QR Code',  min: 11, max: 80, get: a => getFedQrCodeText(a, equipmentQrLookup) || '' },
      { header: 'Fed From',     min: 10, max: 80, get: a => a['Supply From'] || '' },
      { header: 'Room',         min: 5,  max: 40, get: a => a['Room'] || '' },
      { header: 'Voltage',      min: 10, max: 80, get: a => ((a['Voltage Rating']  || '') + ' ' + (a['Voltage Rating (UoM)']  || '')).trim() },
      { header: 'Amperage',     min: 10, max: 80, get: a => ((a['Amperage Rating'] || '') + ' ' + (a['Amperage Rating (UoM)'] || '')).trim() },
      { header: 'Power',        min: 10, max: 80, get: a => ((a['Power Rating']    || '') + ' ' + (a['Power Rating (UoM)']    || '')).trim() },
      { header: 'Wire',         min: 8,  max: 40, get: a => ((a['Wire Rating']     || '') + ' ' + (a['Wire Rating (UoM)']     || '')).trim() },
      { header: null, fixedPx: 60  }, // Check
      { header: null, fixedPx: 72  }, // Save
    ];

    specs.forEach((spec, i) => {
      const col = cols[i];
      if (!col) return;
      if (spec.fixedPx) {
        col.style.width = spec.fixedPx + 'px';
        return;
      }
      let maxChars = Math.max(spec.min, spec.header ? spec.header.length : 0);
      (assets || []).forEach(a => {
        const v = spec.get(a);
        if (v && v.length > maxChars) maxChars = v.length;
      });
      if (maxChars > spec.max) maxChars = spec.max;
      // +4 ch accounts for input border/padding + a little breathing room.
      col.style.width = (maxChars + 4) + 'ch';
    });
  }

  function buildSwiftRow(asset, equipmentQrLookup) {
    const tr = document.createElement('tr');
    tr.className = 'sld-swift-row';
    const rowId = asset && asset.row_id;
    tr.dataset.rowId = String(rowId);
    tr.dataset.swiftRevision = (asset && asset.swift_revision) || '';
    tr.dataset.hierarchy = String(asset && asset['Hierarchy'] != null ? asset['Hierarchy'] : '');
    const matched = hasIdCheckMatch(asset);
    const hasQrMatch = !!getQrCodeText(asset);
    tr.classList.add(matched ? 'is-matched' : 'is-unmatched');

    const qr = getQrCodeText(asset);
    const fedQr = getFedQrCodeText(asset, equipmentQrLookup);
    const hierarchy = asset['Hierarchy'] != null ? asset['Hierarchy'] : '';
    const eq = asset['Equipment ID'] || '';
    const supply = asset['Supply From'] || '';
    const room = asset['Room'] || '';
    const voltage = asset['Voltage Rating'] || '';
    const voltageUom = asset['Voltage Rating (UoM)'] || '';
    const amperage = asset['Amperage Rating'] || '';
    const amperageUom = asset['Amperage Rating (UoM)'] || '';
    const power = asset['Power Rating'] || '';
    const powerUom = asset['Power Rating (UoM)'] || '';
    const wire = asset['Wire Rating'] || '';
    const wireUom = asset['Wire Rating (UoM)'] || '';

    const roomInList = !room || swiftRoomLocations.includes(room);
    const roomExtraClass = roomInList ? '' : ' is-offlist';
    const roomTitleAttr = roomInList
      ? ''
      : ' title="Current Room is not in the Buildings_with_SpaceUID list for this building"';
    const roomCell = hasQrMatch
      ? `<input type="text" list="sld-swift-room-locations" class="sld-swift-input${roomExtraClass}" data-field="Room" value="${escapeHtml(room)}" autocomplete="off"${roomTitleAttr}>`
      : `<input type="text" class="sld-swift-input" data-field="Room" value="" disabled title="No captured asset — Room is only editable when a QR match exists">`;

    const checkCell = matched
      ? '<span class="sld-swift-check ok" title="SLD row matches a captured asset" aria-label="matched">&#10003;</span>'
      : '<span class="sld-swift-check bad" title="No matching captured asset" aria-label="unmatched">&#10007;</span>';

    tr.innerHTML = `
      <td class="col-hierarchy"><span class="sld-swift-ro">${escapeHtml(String(hierarchy))}</span></td>
      <td class="col-qr"><span class="sld-swift-ro sld-swift-qr">${escapeHtml(qr || '—')}</span></td>
      <td class="col-equipment"><input type="text" class="sld-swift-input" data-field="Equipment ID" value="${escapeHtml(eq)}" autocomplete="off" spellcheck="false"></td>
      <td class="col-fed-qr"><span class="sld-swift-ro sld-swift-fed-qr">${escapeHtml(fedQr || '—')}</span></td>
      <td class="col-feed"><input type="text" class="sld-swift-input" data-field="Supply From" value="${escapeHtml(supply)}" autocomplete="off" spellcheck="false" placeholder="Parent tag"></td>
      <td class="col-room">${roomCell}</td>
      <td class="col-voltage">
        <div class="sld-swift-pair">
          <input type="text" class="sld-swift-input v" data-field="Voltage Rating" value="${escapeHtml(voltage)}" autocomplete="off">
          <input type="text" class="sld-swift-input u" data-field="Voltage Rating (UoM)" value="${escapeHtml(voltageUom)}" autocomplete="off" aria-label="Voltage unit">
        </div>
      </td>
      <td class="col-amperage">
        <div class="sld-swift-pair">
          <input type="text" class="sld-swift-input v" data-field="Amperage Rating" value="${escapeHtml(amperage)}" autocomplete="off">
          <input type="text" class="sld-swift-input u" data-field="Amperage Rating (UoM)" value="${escapeHtml(amperageUom)}" autocomplete="off" aria-label="Amperage unit">
        </div>
      </td>
      <td class="col-power">
        <div class="sld-swift-pair">
          <input type="text" class="sld-swift-input v" data-field="Power Rating" value="${escapeHtml(power)}" autocomplete="off">
          <input type="text" class="sld-swift-input u" data-field="Power Rating (UoM)" value="${escapeHtml(powerUom)}" autocomplete="off" aria-label="Power unit">
        </div>
      </td>
      <td class="col-wire">
        <div class="sld-swift-pair">
          <input type="text" class="sld-swift-input v" data-field="Wire Rating" value="${escapeHtml(wire)}" autocomplete="off">
          <input type="text" class="sld-swift-input u" data-field="Wire Rating (UoM)" value="${escapeHtml(wireUom)}" autocomplete="off" aria-label="Wire unit">
        </div>
      </td>
      <td class="col-check"><div class="sld-cell-center">${checkCell}</div></td>
      <td class="col-save">
        <div class="sld-cell-center">
          <button type="button" class="sld-swift-save-btn" title="Save this row" aria-label="Save row" disabled>
            <span class="sld-swift-save-icon" aria-hidden="true">&#128190;</span>
          </button>
          <span class="sld-swift-status" aria-live="polite"></span>
        </div>
      </td>
    `;

    // Snapshot original values for dirty tracking + cancel on confirm.
    const snapshot = {};
    tr.querySelectorAll('.sld-swift-input').forEach((inp) => {
      snapshot[inp.dataset.field] = inp.value;
    });
    tr._swiftSnapshot = snapshot;

    tr.querySelectorAll('.sld-swift-input').forEach((inp) => {
      inp.addEventListener('input', () => markSwiftRowDirty(tr));
    });
    tr.querySelector('.sld-swift-save-btn').addEventListener('click', () => saveSwiftRow(tr));

    // Asset photo popover on QR cell.
    const qrSpan = tr.querySelector('.sld-swift-qr');
    const qrText = getQrCodeText(asset);
    if (qrSpan && qrText && qrText !== '—') {
      assetPhotoPopover.attach(qrSpan, qrText);
      qrSpan.style.cursor = 'zoom-in';
      qrSpan.title = 'Hover to preview asset photo; click to pin';
    }
    const fedQrSpan = tr.querySelector('.sld-swift-fed-qr');
    if (fedQrSpan && fedQr) {
      assetPhotoPopover.attach(fedQrSpan, fedQr);
      fedQrSpan.style.cursor = 'zoom-in';
      fedQrSpan.title = 'Hover to preview Fed From asset photo; click to pin';
    }
    return tr;
  }

  function markSwiftRowDirty(tr) {
    const snap = tr._swiftSnapshot || {};
    let dirty = false;
    tr.querySelectorAll('.sld-swift-input').forEach((inp) => {
      if ((snap[inp.dataset.field] || '') !== (inp.value || '')) dirty = true;
    });
    tr.classList.toggle('is-dirty', dirty);
    const btn = tr.querySelector('.sld-swift-save-btn');
    if (btn) btn.disabled = !dirty;
    // Clear any stale cascade strip when the user keeps editing
    clearSwiftCascadeStrip(tr);
    setSwiftRowStatus(tr, '', '');
  }

  function collectSwiftRowPayload(tr) {
    const payload = {};
    tr.querySelectorAll('.sld-swift-input').forEach((inp) => {
      if (inp.disabled) return;
      payload[inp.dataset.field] = inp.value;
    });
    payload.swift_revision = tr.dataset.swiftRevision || '';
    return payload;
  }

  function setSwiftRowStatus(tr, msg, kind) {
    const el = tr.querySelector('.sld-swift-status');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'sld-swift-status' + (kind ? ' ' + kind : '');
  }

  async function saveSwiftRow(tr, opts) {
    opts = opts || {};
    const rowId = Number(tr.dataset.rowId);
    if (!Number.isFinite(rowId)) return;
    const payload = collectSwiftRowPayload(tr);
    if (opts.cascade) payload.cascade = true;

    const btn = tr.querySelector('.sld-swift-save-btn');
    if (btn) { btn.disabled = true; btn.classList.add('is-saving'); }
    setSwiftRowStatus(tr, 'Saving…', 'pending');

    try {
      const resp = await fetch(SLD_API + '/assets/' + rowId + '/swift-save?building=' + encodeURIComponent(currentBuilding || ''), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));

      if (resp.status === 409 && data.stale_revision) {
        let targetRow = tr;
        if (data.row && tr.parentNode) {
          const updatedTr = buildSwiftRow(data.row);
          tr.parentNode.replaceChild(updatedTr, tr);
          targetRow = updatedTr;
          const idx = (allAssets || []).findIndex(a => a.row_id === data.row.row_id);
          if (idx >= 0) allAssets[idx] = data.row;
          tagSwiftHierarchyGroups();
        }
        setSwiftRowStatus(targetRow, 'Row changed. Review latest values and save again.', 'warn');
        showToast(data.error || 'Row changed. Review latest values and save again.', 'error');
        return;
      }
      if (resp.status === 409 && data.needs_confirmation) {
        renderSwiftCascadeStrip(tr, data);
        setSwiftRowStatus(tr, 'Confirm rename', 'warn');
        return;
      }
      if (!resp.ok) {
        setSwiftRowStatus(tr, data.error || `Save failed (${resp.status})`, 'error');
        showToast(data.error || `Save failed (${resp.status})`, 'error');
        return;
      }

      if (data.row) {
        const updatedTr = buildSwiftRow(data.row);
        tr.parentNode.replaceChild(updatedTr, tr);
        setSwiftRowStatus(updatedTr, 'Saved', 'ok');
        setTimeout(() => setSwiftRowStatus(updatedTr, '', ''), 2500);

        const idx = (allAssets || []).findIndex(a => a.row_id === data.row.row_id);
        if (idx >= 0) allAssets[idx] = data.row;
        tagSwiftHierarchyGroups();
      }

      if (data.children_updated && data.children_updated.length) {
        showToast(`Saved. Updated ${data.children_updated.length} child asset(s).`, 'success');
        await loadBuildingAssets(currentBuilding);
        if (swiftActive) renderSwiftTable(allAssets || []);
      } else {
        showToast('Saved.', 'success');
      }
      if (data.children_failed && data.children_failed.length) {
        showToast(`${data.children_failed.length} child asset(s) did not sync. Check logs.`, 'error');
      }
    } catch (e) {
      setSwiftRowStatus(tr, 'Network error', 'error');
      showToast('Network error: ' + (e && e.message ? e.message : e), 'error');
    } finally {
      if (btn) btn.classList.remove('is-saving');
      // re-enable save only if the row is still in the DOM and dirty
      const stillThere = tr.isConnected ? tr : null;
      if (stillThere) markSwiftRowDirty(stillThere);
    }
  }

  function renderSwiftCascadeStrip(tr, data) {
    clearSwiftCascadeStrip(tr);
    const strip = document.createElement('tr');
    strip.className = 'sld-swift-cascade-strip';
    const names = (data.children || []).map(c => escapeHtml(c['Equipment ID'] || ('#' + c.row_id))).join(', ');
    strip.innerHTML = `
      <td colspan="12">
        <div class="sld-swift-cascade">
          <div class="sld-swift-cascade-msg">
            <strong>Rename cascade:</strong> ${escapeHtml(data.message || '')}
            <div class="sld-swift-cascade-children">${names}</div>
          </div>
          <div class="sld-swift-cascade-actions">
            <button type="button" class="sld-swift-cascade-cancel">Cancel</button>
            <button type="button" class="sld-swift-cascade-confirm">Rename and update ${(data.children || []).length} children</button>
          </div>
        </div>
      </td>
    `;
    tr.parentNode.insertBefore(strip, tr.nextSibling);
    strip.querySelector('.sld-swift-cascade-cancel').addEventListener('click', () => {
      clearSwiftCascadeStrip(tr);
      setSwiftRowStatus(tr, '', '');
    });
    strip.querySelector('.sld-swift-cascade-confirm').addEventListener('click', () => {
      clearSwiftCascadeStrip(tr);
      saveSwiftRow(tr, { cascade: true });
    });
    tr._swiftCascadeStrip = strip;
  }

  function clearSwiftCascadeStrip(tr) {
    if (tr && tr._swiftCascadeStrip && tr._swiftCascadeStrip.parentNode) {
      tr._swiftCascadeStrip.parentNode.removeChild(tr._swiftCascadeStrip);
    }
    if (tr) tr._swiftCascadeStrip = null;
  }

  const EXPORT_TITLE = 'Review Electrical Asset - Distribution';
  const EXPORT_LOGO_URL = (typeof window !== 'undefined' && window.SLD_LOGO_URL)
    ? window.SLD_LOGO_URL
    : '/static/ubc-facilities_logo.jpg';

  function reportUserName() {
    if (typeof window !== 'undefined' && window.SLD_REPORT_USER) {
      return String(window.SLD_REPORT_USER);
    }
    const meta = document.querySelector('meta[name="acshell-user"]');
    return (meta && meta.content) ? meta.content : 'User';
  }

  function blobToDataURL(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = reject;
      fr.readAsDataURL(blob);
    });
  }

  async function fetchLogoDataUrl() {
    try {
      const resp = await fetch(EXPORT_LOGO_URL);
      if (!resp.ok) return null;
      const blob = await resp.blob();
      return await blobToDataURL(blob);
    } catch (e) { return null; }
  }

  async function fetchLogoArrayBuffer() {
    try {
      const resp = await fetch(EXPORT_LOGO_URL);
      if (!resp.ok) return null;
      const blob = await resp.blob();
      return await blob.arrayBuffer();
    } catch (e) { return null; }
  }

  function buildingDisplayName() {
    const match = (buildingsData || []).find(x => x.building === currentBuilding);
    return (match && match.display) || currentBuilding || '';
  }

  function safeFilenamePart(s) {
    return String(s || '').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'export';
  }

  // Build an export-ready clone of the live SLD SVG: bbox-fitted viewBox,
  // zoom transform stripped, find-frames removed, standalone CSS injected.
  // Returns { clone, vbW, vbH } or null when there is no diagram content.
  // Shared by the PDF export and the xlsx diagram embed.
  function buildDiagramSvgClone() {
    const liveSvg = container && container.querySelector('svg');
    if (!liveSvg || !g) return null;
    const groupNode = g.node();
    const bbox = groupNode.getBBox();
    if (!bbox.width || !bbox.height) return null;

    // Clone SVG so the live diagram is untouched.
    const clone = liveSvg.cloneNode(true);
    const cloneG = clone.querySelector('g');
    if (cloneG) cloneG.removeAttribute('transform');
    const pad = 24;
    const vbX = bbox.x - pad, vbY = bbox.y - pad;
    const vbW = bbox.width + pad * 2, vbH = bbox.height + pad * 2;
    clone.setAttribute('viewBox', vbX + ' ' + vbY + ' ' + vbW + ' ' + vbH);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

    // The live page styles SVG elements via `.sld-pane …` selectors. Inside
    // a standalone SVG-as-image those selectors don't match, so anything
    // relying on CSS for fill/opacity (notably `.sld-find-node-frame` and
    // `.link`) renders with default fill (black). Strip those frames and
    // inject a minimal stylesheet whose selectors match in the standalone
    // SVG, so the rasterized output matches what's on screen.
    clone.querySelectorAll('.sld-find-node-frame').forEach(n => n.remove());
    const exportCss = '\n' +
      '.link { fill: none; stroke: #a0b4c8; stroke-width: 2; }\n' +
      '.link-ambiguous { fill: none; stroke: #e8913a; stroke-width: 2; stroke-dasharray: 6 3; }\n' +
      ".node-label { fill: #ffffff; font-size: 11px; font-weight: 700; text-anchor: middle; dominant-baseline: central; font-family: 'Segoe UI', system-ui, sans-serif; }\n" +
      ".node-rating { fill: #6b7c93; font-size: 9px; font-weight: 500; text-anchor: middle; dominant-baseline: hanging; font-family: 'Segoe UI', system-ui, sans-serif; }\n" +
      ".node-qr-code { fill: #0d1b3e; font-size: 9px; font-weight: 700; text-anchor: middle; font-family: 'Segoe UI', system-ui, sans-serif; }\n" +
      '.sld-match circle { stroke: #ffffff; stroke-width: 1.5; }\n' +
      '.sld-match.ok circle { fill: #10b981; }\n' +
      '.sld-match.bad circle { fill: #ef4444; }\n' +
      '.sld-match text { fill: #ffffff; font-size: 10px; font-weight: 700; text-anchor: middle; dominant-baseline: central; }\n' +
      '.sld-collapse-badge rect { fill: #f1f5f9; stroke: #94a3b8; stroke-width: 1; }\n' +
      '.sld-collapse-badge text { font-size: 10px; font-weight: 700; fill: #334155; text-anchor: middle; dominant-baseline: central; }\n' +
      '.sld-collapse-badge.expanded rect { fill: rgba(241,245,249,0.7); stroke: #cbd5e1; }\n' +
      '.sld-collapse-badge.expanded text { fill: #64748b; }\n';
    const styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    styleEl.textContent = exportCss;
    clone.insertBefore(styleEl, clone.firstChild);
    return { clone, vbW, vbH };
  }

  // Rasterize a buildDiagramSvgClone() result onto a white canvas. Returns
  // { dataUrl, pixelWidth, pixelHeight }. renderScale supersamples for
  // sharpness; the effective scale is capped so very large trees stay
  // inside browser canvas dimension limits.
  async function rasterizeDiagramClone(cloneInfo, renderScale) {
    const MAX_DIM = 16000;
    const scale = Math.min(renderScale, MAX_DIM / Math.max(1, cloneInfo.vbW, cloneInfo.vbH));
    const { clone, vbW, vbH } = cloneInfo;
    clone.setAttribute('width', vbW * scale);
    clone.setAttribute('height', vbH * scale);

    const xml = new XMLSerializer().serializeToString(clone);
    const svgBlob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n', xml], { type: 'image/svg+xml;charset=utf-8' });
    const svgUrl = URL.createObjectURL(svgBlob);

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(vbW * scale));
    canvas.height = Math.max(1, Math.round(vbH * scale));
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(svgUrl);
        resolve();
      };
      img.onerror = (err) => { URL.revokeObjectURL(svgUrl); reject(err); };
      img.src = svgUrl;
    });
    return { dataUrl: canvas.toDataURL('image/png'), pixelWidth: canvas.width, pixelHeight: canvas.height };
  }

  // Composite the on-page legend strip (#sld-legend-bar) above a rasterized
  // diagram PNG. The legend items are self-contained inline SVGs in
  // sld_panel.html, so each is serialized and drawn onto a taller canvas
  // followed by the tree image. Best-effort: any failure returns the
  // original raster unchanged (the export then simply has no legend).
  async function composeLegendOntoDiagramPng(raster) {
    try {
      const bar = document.getElementById('sld-legend-bar');
      if (!bar || !raster || !raster.dataUrl) return raster;
      const items = Array.from(bar.querySelectorAll('.sld-legend-item'));
      if (!items.length) return raster;

      // The diagram capture is 2x supersampled; draw the legend at the same
      // scale so it lands at on-screen size when Excel shows the image at half.
      const SCALE = 2;
      const PAD = 12 * SCALE;
      const GAP = 18 * SCALE;
      const ICON_GAP = 6 * SCALE;
      const FONT_ITEM = (11 * SCALE) + 'px "Segoe UI", system-ui, sans-serif';
      const FONT_TITLE = '700 ' + (11 * SCALE) + 'px "Segoe UI", system-ui, sans-serif';

      function loadImage(src) {
        return new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = reject;
          img.src = src;
        });
      }

      const entries = [];
      for (const item of items) {
        const svg = item.querySelector('svg');
        if (!svg) continue;
        const labelHolder = item.cloneNode(true);
        const heldSvg = labelHolder.querySelector('svg');
        if (heldSvg) heldSvg.remove();
        const label = (labelHolder.textContent || '').trim();
        const clone = svg.cloneNode(true);
        const w = (parseFloat(svg.getAttribute('width')) || 24) * SCALE;
        const h = (parseFloat(svg.getAttribute('height')) || 20) * SCALE;
        clone.setAttribute('width', String(w));
        clone.setAttribute('height', String(h));
        clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        const xml = new XMLSerializer().serializeToString(clone);
        const url = URL.createObjectURL(new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n', xml], { type: 'image/svg+xml;charset=utf-8' }));
        try {
          entries.push({ img: await loadImage(url), w, h, label });
        } finally {
          URL.revokeObjectURL(url);
        }
      }
      if (!entries.length) return raster;

      const treeImg = await loadImage(raster.dataUrl);

      const measure = document.createElement('canvas').getContext('2d');
      measure.font = FONT_TITLE;
      const titleW = measure.measureText('Legend').width;
      measure.font = FONT_ITEM;
      let legendW = PAD + titleW + GAP;
      let rowH = 0;
      for (const e of entries) {
        legendW += e.w + ICON_GAP + measure.measureText(e.label).width + GAP;
        rowH = Math.max(rowH, e.h);
      }
      const stripH = rowH + PAD * 1.5;

      const outW = Math.max(raster.pixelWidth, Math.ceil(legendW + PAD));
      const outH = raster.pixelHeight + stripH;
      const canvas = document.createElement('canvas');
      canvas.width = outW;
      canvas.height = outH;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, outW, outH);

      const midY = stripH / 2 - PAD * 0.25;
      ctx.textBaseline = 'middle';
      ctx.font = FONT_TITLE;
      ctx.fillStyle = '#0d1b3e';
      let x = PAD;
      ctx.fillText('Legend', x, midY);
      x += titleW + GAP;
      ctx.font = FONT_ITEM;
      for (const e of entries) {
        ctx.drawImage(e.img, x, midY - e.h / 2, e.w, e.h);
        x += e.w + ICON_GAP;
        ctx.fillStyle = '#334155';
        ctx.fillText(e.label, x, midY);
        x += measure.measureText(e.label).width + GAP;
      }
      ctx.strokeStyle = '#e2e8f0';
      ctx.lineWidth = SCALE;
      ctx.beginPath();
      ctx.moveTo(PAD, stripH - PAD * 0.5);
      ctx.lineTo(outW - PAD, stripH - PAD * 0.5);
      ctx.stroke();

      ctx.drawImage(treeImg, 0, stripH);
      return { dataUrl: canvas.toDataURL('image/png'), pixelWidth: outW, pixelHeight: outH };
    } catch (err) {
      console.warn('[sld] legend composition skipped:', err);
      return raster;
    }
  }

  // Capture the diagram with every node expanded, for the xlsx export.
  // The expand -> render -> clone -> restore cycle runs in one synchronous
  // block: the expanded tree is never painted on screen, localStorage is
  // never written (ensureCollapseStateForBuilding only persists on first
  // render per building), and the zoom transform is unaffected.
  async function captureExpandAllDiagramPng() {
    if (!container || !container.querySelector('svg') || !g) return null;
    if (!allAssets || !allAssets.length) return null;
    const prevCollapsed = collapsedSet;
    const prevInitFor = collapseInitializedFor;
    const diagramView = $('sld-diagram-view');
    const wasHidden = diagramView && !diagramView.classList.contains('active');
    let cloneInfo = null;
    try {
      if (wasHidden) {
        // Swift Over view: the diagram pane is display:none and getBBox
        // would return zeros. Reveal it invisibly for the measurement;
        // nothing paints because this block never yields to the browser.
        diagramView.style.visibility = 'hidden';
        diagramView.classList.add('active');
      }
      collapsedSet = new Set();
      collapseInitializedFor = currentBuilding;
      renderDiagram(allAssets);
      cloneInfo = buildDiagramSvgClone();
    } finally {
      collapsedSet = prevCollapsed;
      collapseInitializedFor = prevInitFor;
      try { renderDiagram(allAssets); } catch (e) { /* state vars already restored */ }
      if (wasHidden) {
        diagramView.classList.remove('active');
        diagramView.style.visibility = '';
      }
    }
    if (!cloneInfo) return null;
    const raster = await rasterizeDiagramClone(cloneInfo, 2);
    // The xlsx embed should mirror the page: legend strip above the tree.
    return composeLegendOntoDiagramPng(raster);
  }

  async function exportSldPdf() {
    if (!window.jspdf || !window.jspdf.jsPDF) {
      showToast('PDF library failed to load. Refresh the page.', 'error');
      return;
    }
    if (!allAssets || !allAssets.length) {
      showToast('Load a building before exporting.', 'error');
      return;
    }
    // When the user is in Switch Over (table) view, export the table instead of
    // the diagram so the auto-fit columns + hierarchy banding visible on screen
    // are reflected in the PDF.
    const swiftToggle = $('sld-swift-over-toggle');
    if (swiftToggle && swiftToggle.checked) {
      return exportSwiftTablePdf();
    }
    const svgEl = container && container.querySelector('svg');
    if (!svgEl || !g) {
      showToast('Diagram is not ready.', 'error');
      return;
    }

    const pdfBtn = $('sld-export-pdf-btn');
    if (pdfBtn) pdfBtn.disabled = true;
    try {
      const cloneInfo = buildDiagramSvgClone();
      if (!cloneInfo) {
        showToast('Diagram has no content to export.', 'error');
        return;
      }
      // renderScale 2 supersamples for sharper PDF rasterization.
      const raster = await rasterizeDiagramClone(cloneInfo, 2);
      const reportRaster = await composeLegendOntoDiagramPng(raster);
      const pngDataUrl = reportRaster.dataUrl;

      const { jsPDF } = window.jspdf;
      const pdfW = 297, pdfH = 210; // A4 landscape mm
      const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });

      const pagePad = 7;
      const boardX = pagePad;
      const boardY = pagePad;
      const boardW = pdfW - pagePad * 2;
      const boardH = pdfH - pagePad * 2;
      const contentPad = 8;
      const headerH = 26;
      const frameY = boardY + headerH + 5;
      const footerH = 7;
      const margin = boardX + contentPad;
      const accentW = 3;

      doc.setFillColor(248, 250, 252);
      doc.rect(0, 0, pdfW, pdfH, 'F');

      doc.setFillColor(255, 255, 255);
      doc.setDrawColor(203, 213, 225);
      doc.setLineWidth(0.35);
      if (typeof doc.roundedRect === 'function') {
        doc.roundedRect(boardX, boardY, boardW, boardH, 4, 4, 'FD');
      } else {
        doc.rect(boardX, boardY, boardW, boardH, 'FD');
      }

      doc.setFillColor(0, 33, 69);
      if (typeof doc.roundedRect === 'function') {
        doc.roundedRect(boardX, boardY, accentW, boardH, 2, 2, 'F');
      } else {
        doc.rect(boardX, boardY, accentW, boardH, 'F');
      }

      // Logo (square)
      const logoData = await fetchLogoDataUrl();
      const logoSize = 16; // mm, square
      if (logoData) {
        try { doc.addImage(logoData, 'JPEG', margin, boardY + 6, logoSize, logoSize); } catch (e) { /* ignore */ }
      }

      // Title
      doc.setTextColor(0, 33, 69);
      doc.setFontSize(16);
      doc.setFont(undefined, 'bold');
      doc.text(EXPORT_TITLE, margin + 32, boardY + 14);

      // Subtitle: building + date
      doc.setFontSize(10);
      doc.setFont(undefined, 'normal');
      doc.setTextColor(100);
      const building = buildingDisplayName();
      const createdAt = new Date();
      const dateStr = createdAt.toLocaleDateString();
      const createdAtStr = createdAt.toLocaleString();
      doc.text((building ? 'Building: ' + building : 'All buildings') + '   |   ' + dateStr, margin + 32, boardY + 21);

      // Separator
      doc.setDrawColor(226, 232, 240);
      doc.setLineWidth(0.3);
      doc.line(margin, boardY + headerH, boardX + boardW - contentPad, boardY + headerH);
      doc.setTextColor(0);

      // Legend and diagram image, fit-to-board preserving composed raster ratio.
      const availW = boardW - contentPad * 2;
      const availH = boardY + boardH - contentPad - footerH - frameY;
      const imagePad = 2;
      const imageMaxW = availW - imagePad * 2;
      const imageMaxH = availH - imagePad * 2;
      const rasterW = reportRaster.pixelWidth || raster.pixelWidth || cloneInfo.vbW || 1;
      const rasterH = reportRaster.pixelHeight || raster.pixelHeight || cloneInfo.vbH || 1;
      const aspect = rasterW / rasterH;
      let imgW, imgH;
      if (imageMaxW / imageMaxH > aspect) {
        imgH = imageMaxH;
        imgW = imgH * aspect;
      } else {
        imgW = imageMaxW;
        imgH = imgW / aspect;
      }
      const imgX = margin + imagePad + (imageMaxW - imgW) / 2;
      const imgY = frameY + imagePad;
      doc.addImage(pngDataUrl, 'PNG', imgX, imgY, imgW, imgH);
      doc.setFillColor(255, 255, 255);
      doc.setDrawColor(226, 232, 240);
      doc.setLineWidth(0.25);
      if (typeof doc.roundedRect === 'function') {
        doc.roundedRect(margin, frameY, availW, availH, 3, 3, 'S');
      } else {
        doc.rect(margin, frameY, availW, availH, 'S');
      }

      const footerY = boardY + boardH - contentPad + 1;
      doc.setDrawColor(226, 232, 240);
      doc.setLineWidth(0.25);
      doc.line(margin, footerY - 4.2, boardX + boardW - contentPad, footerY - 4.2);
      doc.setFontSize(8);
      doc.setFont(undefined, 'normal');
      doc.setTextColor(100);
      doc.text('Generated by: ' + reportUserName(), margin, footerY);
      doc.text('Created: ' + createdAtStr, boardX + boardW - contentPad, footerY, { align: 'right' });

      const filename = 'EL_SLD_' + safeFilenamePart(currentBuilding) + '.pdf';
      doc.save(filename);
    } catch (err) {
      console.error('[sld] PDF export failed', err);
      showToast('PDF export failed: ' + (err && err.message ? err.message : 'unknown error'), 'error');
    } finally {
      if (pdfBtn) pdfBtn.disabled = false;
    }
  }

  // PDF export of the Switch Over table. Mirrors the on-screen behavior:
  // columns auto-fit the widest value (jspdf-autotable's `cellWidth: 'wrap'`
  // measures content), and rows are banded by Hierarchy so each group reads
  // as a single block.
  async function exportSwiftTablePdf() {
    if (!window.jspdf || !window.jspdf.jsPDF) {
      showToast('PDF library failed to load. Refresh the page.', 'error');
      return;
    }
    const { jsPDF } = window.jspdf;
    const probeDoc = new jsPDF();
    if (typeof probeDoc.autoTable !== 'function') {
      showToast('Table-PDF plugin missing. Refresh the page.', 'error');
      return;
    }
    if (!allAssets || !allAssets.length) {
      showToast('Load a building before exporting.', 'error');
      return;
    }
    const pdfBtn = $('sld-export-pdf-btn');
    if (pdfBtn) pdfBtn.disabled = true;
    try {
      const headers = ['Hierarchy', 'Equip QR Code', 'Equipment ID', 'Fed QR Code', 'Fed From', 'Room', 'Voltage', 'Amperage', 'Power', 'Wire', 'Check'];
      const equipmentQrLookup = buildEquipmentQrLookup(allAssets || []);
      const rows = allAssets.map(a => {
        const matched = hasIdCheckMatch(a);
        return [
          (a['Hierarchy'] != null ? a['Hierarchy'] : ''),
          getQrCodeText(a) || '',
          a['Equipment ID'] || '',
          getFedQrCodeText(a, equipmentQrLookup) || '',
          a['Supply From'] || '',
          a['Room'] || '',
          ((a['Voltage Rating']  || '') + ' ' + (a['Voltage Rating (UoM)']  || '')).trim(),
          ((a['Amperage Rating'] || '') + ' ' + (a['Amperage Rating (UoM)'] || '')).trim(),
          ((a['Power Rating']    || '') + ' ' + (a['Power Rating (UoM)']    || '')).trim(),
          ((a['Wire Rating']     || '') + ' ' + (a['Wire Rating (UoM)']     || '')).trim(),
          matched ? 'Y' : 'N',
        ];
      });

      // Pre-compute hierarchy band index for each row so didParseCell can
      // alternate the fill on Hierarchy boundaries.
      const bandColors = [[248, 250, 252], [232, 238, 247]];
      const bandFor = [];
      let bandIdx = 0;
      let prevH = null;
      rows.forEach((r, i) => {
        const h = String(r[0] != null ? r[0] : '');
        if (i > 0 && h !== prevH) bandIdx = (bandIdx + 1) % bandColors.length;
        prevH = h;
        bandFor.push(bandIdx);
      });

      const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
      const pdfW = doc.internal.pageSize.getWidth();
      const margin = 10;

      // Header band (logo + title + subtitle), matching the diagram PDF.
      const logoData = await fetchLogoDataUrl();
      const logoSize = 16;
      if (logoData) {
        try { doc.addImage(logoData, 'JPEG', margin, 6, logoSize, logoSize); } catch (e) { /* ignore */ }
      }
      doc.setTextColor(0, 33, 69);
      doc.setFontSize(16);
      doc.setFont(undefined, 'bold');
      doc.text(EXPORT_TITLE, margin + 22, 14);

      doc.setFontSize(10);
      doc.setFont(undefined, 'normal');
      doc.setTextColor(100);
      const building = buildingDisplayName();
      const dateStr = new Date().toLocaleDateString();
      const distinctHierarchies = new Set(rows.map(r => String(r[0] != null ? r[0] : '').trim()).filter(Boolean)).size;
      const subtitle = (building ? 'Building: ' + building : 'All buildings')
        + '   |   ' + dateStr
        + '   |   Assets: ' + rows.length
        + '   |   Hierarchies: ' + distinctHierarchies;
      doc.text(subtitle, margin + 22, 21);

      doc.setDrawColor(220);
      doc.setLineWidth(0.3);
      doc.line(margin, 24, pdfW - margin, 24);

      doc.autoTable({
        head: [headers],
        body: rows,
        startY: 28,
        theme: 'grid',
        margin: { left: margin, right: margin },
        styles: {
          font: 'helvetica',
          fontSize: 8,
          cellPadding: 1.6,
          overflow: 'linebreak',
          valign: 'middle',
          lineColor: [226, 232, 240],
          lineWidth: 0.1,
          textColor: [15, 23, 42],
        },
        headStyles: {
          fillColor: [0, 33, 69],
          textColor: [255, 255, 255],
          halign: 'center',
          fontStyle: 'bold',
          lineColor: [0, 33, 69],
        },
        columnStyles: {
          // Hierarchy + Check are centered; everything else left-aligned.
          0: { halign: 'center', cellWidth: 'wrap' },
          1: { cellWidth: 'wrap' },
          2: { cellWidth: 'wrap' },
          3: { cellWidth: 'wrap' },
          4: { cellWidth: 'wrap' },
          5: { cellWidth: 'wrap' },
          6: { cellWidth: 'wrap' },
          7: { cellWidth: 'wrap' },
          8: { cellWidth: 'wrap' },
          9: { cellWidth: 'wrap' },
          10: { halign: 'center', cellWidth: 'wrap', fontStyle: 'bold' },
        },
        // Per-row hierarchy banding + Check-cell color + Fed header tint.
        didParseCell: (data) => {
          if (data.section === 'head') {
            // Fed QR Code (col 3) + Fed From (col 4) tinted #FCD5B4 to
            // match the on-screen Switch Over editor header pairing.
            if (data.column.index === 3 || data.column.index === 4) {
              data.cell.styles.fillColor = [252, 213, 180];
              data.cell.styles.textColor = [15, 23, 42];
            }
            return;
          }
          if (data.section !== 'body') return;
          const idx = data.row.index;
          if (idx >= 0 && idx < bandFor.length) {
            data.cell.styles.fillColor = bandColors[bandFor[idx]];
          }
          if (data.column.index === 10) {
            const v = String(data.cell.raw || '').trim().toUpperCase();
            data.cell.styles.textColor = v === 'Y' ? [16, 185, 129] : [239, 68, 68];
          }
        },
      });

      const filename = 'EL_SLD_Table_' + safeFilenamePart(currentBuilding) + '.pdf';
      doc.save(filename);
    } catch (err) {
      console.error('[sld] Table PDF export failed', err);
      showToast('PDF export failed: ' + (err && err.message ? err.message : 'unknown error'), 'error');
    } finally {
      if (pdfBtn) pdfBtn.disabled = false;
    }
  }

  async function exportSwiftExcel() {
    // Server-side download path. The client-side ExcelJS+blob path produced
    // "Couldn't download — Network issue" failures in Edge inside the
    // unified Dashboard iframe (xlsx-specific; PDF on the same iframe
    // works). Edge's iframe download manager selectively blocks Office
    // MIME types delivered via blob URLs, and FileSaver.js did not
    // resolve it. Routing through the server endpoint sends a normal
    // HTTP response with Content-Disposition headers — the canonical
    // download path that works wherever any download works.
    if (!currentBuilding) {
      showToast('Load a building before exporting.', 'error');
      return;
    }
    // Cache-bust marker (visible in DevTools to confirm the latest JS is
    // loaded). If you see a number lower than this in the console, the
    // browser is serving a stale sld.js — Ctrl+Shift+Delete to clear cache.
    console.log('[sld] xlsx export build=20260612-diagram-legend server-side');
    const xlsBtn = $('sld-export-xlsx-btn');
    if (xlsBtn) xlsBtn.disabled = true;
    // Best-effort: capture the diagram in Expand All mode and hand it to the
    // server so it can be embedded below the table. Any failure here falls
    // through to the table-only download — never block the spreadsheet.
    let diagramToken = '';
    try {
      const png = await captureExpandAllDiagramPng();
      if (png && png.dataUrl) {
        const resp = await fetch('/sld/api/diagram-image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ png: png.dataUrl }),
        });
        if (resp.ok) {
          diagramToken = (((await resp.json()) || {}).token) || '';
        } else {
          console.warn('[sld] diagram upload failed (' + resp.status + '); exporting table only');
        }
      }
    } catch (err) {
      console.warn('[sld] diagram capture skipped:', err);
    }
    try {
      let url = '/sld/api/download-xlsx?building=' + encodeURIComponent(currentBuilding);
      if (diagramToken) url += '&diagram_token=' + encodeURIComponent(diagramToken);
      // Synthetic <a download> click on a real HTTP URL — the same pattern
      // jsPDF uses internally for the (already-working) PDF export. This
      // is more reliable than window.location.href inside a sandboxed
      // iframe because the browser interprets it as an explicit download
      // request, not a navigation that happens to return attachment headers.
      const aTag = document.createElement('a');
      aTag.href = url;
      aTag.download = 'EL_Assets_' + safeFilenamePart(currentBuilding) + '.xlsx';
      aTag.rel = 'noopener';
      document.body.appendChild(aTag);
      aTag.click();
      aTag.remove();
    } catch (err) {
      console.error('[sld] Excel export failed', err);
      showToast('Excel export failed: ' + (err && err.message ? err.message : 'unknown error'), 'error');
    } finally {
      // Re-enable the button after the click is dispatched; the actual
      // download happens asynchronously and doesn't tie up the UI.
      setTimeout(() => { if (xlsBtn) xlsBtn.disabled = false; }, 2000);
    }
    return;
  }

  // Legacy ExcelJS-based client export. Kept available as
  // window.__sldClientExcelExport for emergency rollback if the
  // server-side endpoint ever needs to be bypassed. Not wired into the
  // UI; the dead-code path may be removed once the server endpoint has
  // proven stable in production.
  async function exportSwiftExcelClientLegacy() {
    if (typeof window.ExcelJS === 'undefined') {
      showToast('Excel library failed to load. Refresh the page.', 'error');
      return;
    }
    if (!allAssets || !allAssets.length) {
      showToast('Load a building before exporting.', 'error');
      return;
    }

    const xlsBtn = $('sld-export-xlsx-btn');
    if (xlsBtn) xlsBtn.disabled = true;
    try {
      const wb = new window.ExcelJS.Workbook();
      wb.creator = 'EL Dashboard';
      wb.created = new Date();
      const ws = wb.addWorksheet('Assets', {
        views: [{ state: 'frozen', ySplit: 6, showGridLines: false }],
        pageSetup: {
          orientation: 'landscape',
          fitToPage: true,
          fitToWidth: 1,
          fitToHeight: 0,
          horizontalCentered: true,
          printTitlesRow: '6:6',
          margins: { left: 0.4, right: 0.4, top: 0.5, bottom: 0.5, header: 0.3, footer: 0.3 },
        },
      });

      // Logo at top-left, rendered as a perfect square
      const logoBuf = await fetchLogoArrayBuffer();
      if (logoBuf) {
        try {
          const imgId = wb.addImage({ buffer: logoBuf, extension: 'jpeg' });
          ws.addImage(imgId, {
            tl: { col: 0.1, row: 0.15 },
            ext: { width: 60, height: 60 },
          });
        } catch (e) { /* logo optional */ }
      }

      // Title block (col B..K, rows 1..3)
      ws.mergeCells('B1:K3');
      const titleCell = ws.getCell('B1');
      titleCell.value = EXPORT_TITLE;
      titleCell.font = { name: 'Calibri', size: 16, bold: true, color: { argb: 'FF002145' } };
      titleCell.alignment = { vertical: 'middle', horizontal: 'left', indent: 1 };

      // Subtitle (row 4)
      ws.mergeCells('B4:K4');
      const subCell = ws.getCell('B4');
      const building = buildingDisplayName();
      const dateStr = new Date().toLocaleDateString();
      subCell.value = (building ? 'Building: ' + building : 'All buildings') + '   |   Generated ' + dateStr;
      subCell.font = { name: 'Calibri', size: 10, italic: true, color: { argb: 'FF64748B' } };
      subCell.alignment = { vertical: 'middle', horizontal: 'left', indent: 1 };

      // Set row heights for header band
      ws.getRow(1).height = 22;
      ws.getRow(2).height = 22;
      ws.getRow(3).height = 22;
      ws.getRow(4).height = 18;
      ws.getRow(5).height = 6;

      // Header row at row 6 — same column order as the Swift web table.
      const headers = ['Hierarchy', 'Equip QR Code', 'Equipment ID', 'Fed QR Code', 'Fed From', 'Room', 'Voltage', 'Amperage', 'Power', 'Wire', 'Check'];
      const headerRow = ws.getRow(6);
      headers.forEach((h, i) => {
        const cell = headerRow.getCell(i + 1);
        const isFed = (i === 3 || i === 4);
        cell.value = h;
        cell.font = { name: 'Calibri', size: 11, bold: true, color: { argb: isFed ? 'FF002145' : 'FFFFFFFF' } };
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: isFed ? 'FFFCD5B4' : 'FF002145' } };
        cell.alignment = { horizontal: 'center', vertical: 'middle' };
        cell.border = {
          top:    { style: 'thin', color: { argb: 'FF002145' } },
          bottom: { style: 'thin', color: { argb: 'FF002145' } },
          left:   { style: 'thin', color: { argb: 'FFFFFFFF' } },
          right:  { style: 'thin', color: { argb: 'FFFFFFFF' } },
        };
      });
      headerRow.height = 22;

      // Data rows. Pre-build value rows so we can size columns to the widest
      // entry and band rows by Hierarchy.
      const rowBorderColor = 'FFE2E8F0';
      // Two soft fills, one per hierarchy band, alternated as the Hierarchy
      // value changes. Mirrors the on-screen group-shadow grouping.
      const bandFills = ['FFF8FAFC', 'FFEEF2F8'];
      const equipmentQrLookup = buildEquipmentQrLookup(allAssets || []);
      const valueRows = allAssets.map(a => {
        const matched = hasIdCheckMatch(a);
        return {
          matched,
          values: [
            (a['Hierarchy'] != null ? a['Hierarchy'] : ''),
            getQrCodeText(a) || '',
            a['Equipment ID'] || '',
            getFedQrCodeText(a, equipmentQrLookup) || '',
            a['Supply From'] || '',
            a['Room'] || '',
            ((a['Voltage Rating']  || '') + ' ' + (a['Voltage Rating (UoM)']  || '')).trim(),
            ((a['Amperage Rating'] || '') + ' ' + (a['Amperage Rating (UoM)'] || '')).trim(),
            ((a['Power Rating']    || '') + ' ' + (a['Power Rating (UoM)']    || '')).trim(),
            ((a['Wire Rating']     || '') + ' ' + (a['Wire Rating (UoM)']     || '')).trim(),
            matched ? '✓' : '✗',
          ],
        };
      });

      let bandIdx = 0;
      let prevHierarchy = null;
      valueRows.forEach((vr, idx) => {
        const r = 7 + idx;
        const hier = String(vr.values[0] != null ? vr.values[0] : '');
        if (idx > 0 && hier !== prevHierarchy) bandIdx = (bandIdx + 1) % bandFills.length;
        prevHierarchy = hier;
        const fillArgb = bandFills[bandIdx];
        const row = ws.getRow(r);
        vr.values.forEach((v, i) => {
          const cell = row.getCell(i + 1);
          cell.value = v;
          cell.font = { name: 'Calibri', size: 11 };
          cell.alignment = { vertical: 'middle', horizontal: i === 0 || i === 10 ? 'center' : 'left' };
          cell.border = {
            top:    { style: 'thin', color: { argb: rowBorderColor } },
            bottom: { style: 'thin', color: { argb: rowBorderColor } },
            left:   { style: 'thin', color: { argb: rowBorderColor } },
            right:  { style: 'thin', color: { argb: rowBorderColor } },
          };
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: fillArgb } };
        });
        const checkCell = row.getCell(11);
        checkCell.font = { name: 'Calibri', size: 12, bold: true, color: { argb: vr.matched ? 'FF10B981' : 'FFEF4444' } };
        checkCell.alignment = { horizontal: 'center', vertical: 'middle' };
      });

      // Auto-fit each column to the widest cell content (including header).
      // Excel's `width` is in character widths; +3 leaves padding so the
      // column doesn't visually clip values.
      const minWidths = [11, 16, 16, 16, 16, 11, 14, 14, 14, 10, 8]; // baseline floors
      const headerLabels = headers;
      headerLabels.forEach((h, i) => {
        let widest = String(h).length;
        valueRows.forEach(vr => {
          const v = vr.values[i];
          const len = String(v == null ? '' : v).length;
          if (len > widest) widest = len;
        });
        ws.getColumn(i + 1).width = Math.max(minWidths[i], widest + 3);
      });

      const arrayBuffer = await wb.xlsx.writeBuffer();
      const blob = new Blob([arrayBuffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
      const filename = 'EL_Assets_' + safeFilenamePart(currentBuilding) + '.xlsx';
      // Prefer FileSaver.js: it handles Edge's iframe-context download path
      // for Office MIME types reliably (the raw blob+anchor pattern below
      // races with Edge's SmartScreen pre-scan and produces "Network issue"
      // failures even with a generous revoke timeout). Fall back to the
      // manual pattern only if the library is missing for any reason.
      if (typeof window.saveAs === 'function') {
        window.saveAs(blob, filename);
      } else {
        const url = URL.createObjectURL(blob);
        const aTag = document.createElement('a');
        aTag.href = url;
        aTag.download = filename;
        document.body.appendChild(aTag);
        aTag.click();
        aTag.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
    } catch (err) {
      console.error('[sld] Excel export failed', err);
      showToast('Excel export failed: ' + (err && err.message ? err.message : 'unknown error'), 'error');
    } finally {
      if (xlsBtn) xlsBtn.disabled = false;
    }
  }

  function showToast(msg, type) {
    const el = $('sld-toast');
    el.textContent = msg;
    el.className = 'sld-toast ' + type + ' show';
    setTimeout(() => el.classList.remove('show'), 3500);
  }

  function syncSwiftFindHighlight(options) {
    const rows = document.querySelectorAll('#sld-swift-tbody .sld-swift-row');
    let activeRow = null;
    rows.forEach((row) => {
      const isTarget = !!activeFindResultId && row.dataset.rowId === String(activeFindResultId);
      row.classList.toggle('is-find-target', isTarget);
      if (isTarget) activeRow = row;
    });
    if (activeRow && options && options.scrollIntoView) {
      activeRow.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }

  function collectBoundsForRowIds(rowIds) {
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let bottom = -Infinity;
    let found = false;
    (rowIds || []).forEach((rowId) => {
      const info = renderedNodeLookup.get(String(rowId));
      if (!info) return;
      found = true;
      left = Math.min(left, info.left);
      right = Math.max(right, info.right);
      top = Math.min(top, info.top);
      bottom = Math.max(bottom, info.bottom);
    });
    if (!found) return null;
    return {
      x: left,
      y: top,
      width: Math.max(1, right - left),
      height: Math.max(1, bottom - top),
    };
  }

  function buildTransformForBounds(bounds, options) {
    if (!bounds || !container) return null;
    const padding = options && options.padding != null ? options.padding : 40;
    const maxScale = options && options.maxScale != null ? options.maxScale : 1.5;
    const fw = container.clientWidth;
    const fh = container.clientHeight;
    if (!fw || !fh) return null;
    const bw = bounds.width + (padding * 2);
    const bh = bounds.height + (padding * 2);
    const scale = Math.min(fw / bw, fh / bh, maxScale);
    const tx = fw / 2 - (bounds.x + (bounds.width / 2)) * scale;
    const ty = fh / 2 - (bounds.y + (bounds.height / 2)) * scale;
    return d3.zoomIdentity.translate(tx, ty).scale(scale);
  }

  function fitToBounds(bounds, options) {
    const transform = buildTransformForBounds(bounds, options);
    if (!transform || !svgEl || !zoomBehavior) return false;
    const duration = options && options.duration != null ? options.duration : 500;
    svgEl.transition().duration(duration).call(zoomBehavior.transform, transform);
    return true;
  }

  function fitToFocusArea(rowId) {
    if (!rowId) return false;
    const areaIds = new Set([String(rowId)]);
    activeFindContextIds.forEach(id => areaIds.add(String(id)));
    const bounds = collectBoundsForRowIds(areaIds);
    if (!bounds) return false;
    return fitToBounds(bounds, { padding: 72, maxScale: 1.85, duration: 450 });
  }

  function applyFindVisualState() {
    const primaryId = activeFindResultId ? String(activeFindResultId) : '';
    const contextIds = activeFindContextIds || new Set();
    const focusIds = new Set();
    if (primaryId) focusIds.add(primaryId);
    contextIds.forEach(id => focusIds.add(String(id)));
    const hasFocus = focusIds.size > 0;

    if (nodeSelection) {
      nodeSelection
        .classed('sld-find-primary', d => getAssetRowId(d.data) === primaryId)
        .classed('sld-find-context', d => contextIds.has(getAssetRowId(d.data)))
        .classed('sld-find-faded', d => hasFocus && !focusIds.has(getAssetRowId(d.data)));
    }
    const updateLinkFade = (selection) => {
      if (!selection) return;
      selection.classed('sld-find-faded', d => {
        if (!hasFocus) return false;
        const sourceId = getAssetRowId(d.source.data);
        const targetId = getAssetRowId(d.target.data);
        return !focusIds.has(sourceId) && !focusIds.has(targetId);
      });
    };
    updateLinkFade(linkSelection);
    updateLinkFade(ambiguousLinkSelection);

    if (findAreaLayer) {
      findAreaLayer.selectAll('*').remove();
      if (hasFocus) {
        const bounds = collectBoundsForRowIds(focusIds);
        if (bounds) {
          const pad = 26;
          findAreaLayer.append('rect')
            .attr('class', 'sld-find-area-box')
            .attr('x', bounds.x - pad)
            .attr('y', bounds.y - pad)
            .attr('width', bounds.width + (pad * 2))
            .attr('height', bounds.height + (pad * 2))
            .attr('rx', 16);
        }
      }
    }
  }

  function fitToScreen() {
    if (!g || !g.node() || !g.node().childNodes.length) return false;
    const bounds = g.node().getBBox();
    if (!bounds.width || !bounds.height) return false;
    return fitToBounds(bounds, { padding: 40, maxScale: 1.5, duration: 500 });
  }

  function resetZoom() {
    svgEl.transition().duration(500).call(zoomBehavior.transform, d3.zoomIdentity);
  }

  function zoomBy(factor) {
    if (!svgEl || !zoomBehavior) return;
    svgEl.transition().duration(180).call(zoomBehavior.scaleBy, factor);
  }

  function updateZoomLabel(k) {
    const el = $('sld-zoom-level');
    if (el) el.textContent = Math.round(k * 100) + '%';
  }

  function toggleOrientation() {
    orientation = (orientation === 'vertical') ? 'horizontal' : 'vertical';
    try { localStorage.setItem('sld.orientation', orientation); } catch (e) { /* noop */ }
    updateOrientationButton();
    if (allAssets && allAssets.length) {
      renderDiagram(allAssets);
      if (!focusActiveSearchIfPossible()) fitToScreen();
    }
  }

  function updateOrientationButton() {
    const btn = $('sld-orientation-btn');
    if (!btn) return;
    const isVertical = (orientation === 'vertical');
    // The icon reflects the *current* layout state, matching the prior text
    // ("Layout: Vertical" / "Layout: Horizontal"). The title still describes
    // the action that clicking will perform, so the hover hint is unchanged.
    const icon = btn.querySelector('i');
    if (icon) {
      icon.classList.remove('bi-arrow-down-up', 'bi-arrow-left-right');
      icon.classList.add(isVertical ? 'bi-arrow-down-up' : 'bi-arrow-left-right');
    }
    btn.title = isVertical
      ? 'Layout: Vertical \u2014 click to switch to horizontal'
      : 'Layout: Horizontal \u2014 click to switch to vertical';
    btn.setAttribute('aria-label', isVertical ? 'Layout: Vertical' : 'Layout: Horizontal');
  }

  function isSldTabActive() {
    const pane = document.querySelector('.sld-pane');
    if (!pane) return false;
    const rect = pane.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  // Expose a single entry point for tab lazy-init.
  window.SLD = { init };

  function tryBootstrapTabHook() {
    const tabBtn = document.getElementById('tab-sld-btn');
    if (!tabBtn) return false;
    tabBtn.addEventListener('shown.bs.tab', init);
    if (tabBtn.classList.contains('active')) init();
    return true;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryBootstrapTabHook);
  } else {
    tryBootstrapTabHook();
  }
})();
