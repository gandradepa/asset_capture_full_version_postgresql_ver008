/* Life Cycle Assessment dashboard — vanilla JS.
   Gates the tables behind the asset-group dropdown, switches the two tabs,
   live-filters rows, sorts columns, and drives the classification chart filter.
   Each tab (Complete / Incomplete) has its own QR / Asset Tag / Building
   filters; only the Complete tab has the classification bar chart. Within a
   tab the filters are faceted: each one's options reflect the others. No
   frameworks. */
(function () {
  "use strict";

  var select = document.getElementById("group-select");
  var results = document.getElementById("results");
  var emptyState = document.getElementById("empty-state");
  var groupTitle = document.getElementById("group-title");
  var search = document.getElementById("search");
  var tabHint = document.getElementById("tab-hint");
  var resetBtn = document.getElementById("reset-btn");
  var exportBtn = document.getElementById("export-btn");

  var panes = {
    complete: document.getElementById("pane-complete"),
    incomplete: document.getElementById("pane-incomplete"),
  };
  var tabs = {
    complete: document.getElementById("tab-complete"),
    incomplete: document.getElementById("tab-incomplete"),
  };
  var counts = {
    complete: document.getElementById("count-complete"),
    incomplete: document.getElementById("count-incomplete"),
  };
  var HINTS = {
    complete: "These assets have an Installation Date on file.",
    incomplete: "These assets do not have an Installation Date on file.",
  };

  // ---- Building multi-select dropdown --------------------------------------
  // A compact checkbox dropdown that stands in for the old single <select>.
  // Selection lives in a Set of building names, so it survives the faceted
  // re-renders that rebuild the option list. Exposes a select-like contract:
  //   values()      -> sorted array of checked names; [] means "all" (no filter)
  //   setOptions(l) -> rebuild rows from faceted list l, keep checks for names
  //                    still present, drop the rest (self-heal). Returns true
  //                    when the selection set changed.
  //   clear()       -> uncheck everything, reset search + label
  //   onChange(fn)  -> fire fn after any selection change
  function makeBuildingSelect(root) {
    if (!root) return null;
    var toggle = root.querySelector(".ms-toggle");
    var textEl = root.querySelector(".ms-text");
    var panel = root.querySelector(".ms-panel");
    var searchEl = root.querySelector(".ms-search");
    var optionsEl = root.querySelector(".ms-options");
    var selected = {};          // name -> true (the checked set)
    var available = [];         // names currently offered (faceted list)
    var changeCb = null;

    function selectedNames() {
      return Object.keys(selected).sort();
    }
    function syncLabel() {
      var names = selectedNames();
      if (names.length === 0) textEl.textContent = "All buildings";
      else if (names.length === 1) textEl.textContent = names[0];
      else textEl.textContent = names.length + " selected";
    }
    function applySearch() {
      var q = (searchEl.value || "").trim().toLowerCase();
      optionsEl.querySelectorAll(".ms-option").forEach(function (row) {
        var name = row.dataset.name || "";
        row.hidden = q !== "" && name.toLowerCase().indexOf(q) === -1;
      });
    }
    function buildRows() {
      optionsEl.innerHTML = "";
      if (!available.length) {
        var empty = document.createElement("div");
        empty.className = "ms-empty";
        empty.textContent = "No buildings";
        optionsEl.appendChild(empty);
        return;
      }
      available.forEach(function (name) {
        var row = document.createElement("label");
        row.className = "ms-option";
        row.dataset.name = name;
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = name;
        cb.checked = !!selected[name];
        row.classList.toggle("is-on", cb.checked);
        var span = document.createElement("span");
        span.className = "ms-option-name";
        span.textContent = name;
        row.appendChild(cb);
        row.appendChild(span);
        optionsEl.appendChild(row);
        cb.addEventListener("change", function () {
          if (cb.checked) selected[name] = true; else delete selected[name];
          row.classList.toggle("is-on", cb.checked);
          syncLabel();
          if (changeCb) changeCb();
        });
      });
      applySearch();
    }

    function setOptions(list) {
      available = list.slice();
      // Self-heal: drop checked names that are no longer offered.
      var present = {};
      available.forEach(function (n) { present[n] = true; });
      var changed = false;
      Object.keys(selected).forEach(function (n) {
        if (!present[n]) { delete selected[n]; changed = true; }
      });
      buildRows();
      syncLabel();
      return changed;
    }
    function clear() {
      selected = {};
      if (searchEl) searchEl.value = "";
      buildRows();
      syncLabel();
    }
    // Place the fixed panel just under the toggle, clamped to the viewport
    // (flips above the toggle when there's no room below).
    function positionPanel() {
      var r = toggle.getBoundingClientRect();
      var docEl = document.documentElement;
      var vw = docEl.clientWidth, vh = docEl.clientHeight;
      panel.style.minWidth = r.width + "px";
      var pw = panel.offsetWidth, ph = panel.offsetHeight;
      var left = Math.max(8, Math.min(r.left, vw - pw - 8));
      var top = r.bottom + 4;
      if (top + ph > vh - 8 && r.top - 4 - ph > 8) top = r.top - 4 - ph;
      panel.style.left = left + "px";
      panel.style.top = top + "px";
    }
    function openPanel() {
      panel.hidden = false;
      positionPanel();
      toggle.setAttribute("aria-expanded", "true");
      root.classList.add("is-open");
    }
    function closePanel() {
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      root.classList.remove("is-open");
    }
    function togglePanel() { panel.hidden ? openPanel() : closePanel(); }
    // A fixed panel must track the toggle as the page/table scrolls or resizes.
    function reflow() { if (!panel.hidden) positionPanel(); }
    window.addEventListener("scroll", reflow, true);
    window.addEventListener("resize", reflow);

    toggle.addEventListener("click", function (e) { e.stopPropagation(); togglePanel(); });
    panel.addEventListener("click", function (e) { e.stopPropagation(); });
    if (searchEl) searchEl.addEventListener("input", applySearch);
    root.querySelectorAll(".ms-action").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var act = btn.dataset.act;
        if (act === "all") available.forEach(function (n) { selected[n] = true; });
        else selected = {};
        buildRows();
        syncLabel();
        if (changeCb) changeCb();
      });
    });
    // Close on outside-click and Esc (one listener per instance is fine).
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !root.contains(e.target)) closePanel();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panel.hidden) closePanel();
    });

    return {
      values: selectedNames,
      setOptions: setOptions,
      clear: clear,
      onChange: function (fn) { changeCb = fn; },
    };
  }

  // Per-tab filter controls. classFilter (the chart) only exists for Complete.
  function makeControls(paneName) {
    var chart = document.getElementById("class-chart-" + paneName);
    return {
      qr: document.getElementById("filter-qr-" + paneName),
      tag: document.getElementById("filter-tag-" + paneName),
      building: makeBuildingSelect(document.getElementById("filter-building-" + paneName)),
      chart: chart,
      chartBars: chart ? Array.prototype.slice.call(chart.querySelectorAll(".chart-bar")) : [],
      classFilter: null,
      // Capture status dropdown + Capture Date picker (both tabs).
      capture: document.getElementById("filter-capture-" + paneName),
      captureDate: document.getElementById("filter-capture-date-" + paneName),
    };
  }
  var controls = {
    complete: makeControls("complete"),
    incomplete: makeControls("incomplete"),
  };

  // Default ordering applied on load, on group change, and on Reset.
  var DEFAULT_SORT = { key: "years", dir: "desc" };

  var activeTab = "complete";
  var sortState = {
    complete: cloneSort(DEFAULT_SORT),
    incomplete: cloneSort(DEFAULT_SORT),
  };

  function cloneSort(s) { return { key: s.key, dir: s.dir }; }
  function inputVal(el) { return el ? (el.value || "").trim().toLowerCase() : ""; }
  // The global "Search this table" box was removed; per-tab filters remain.
  function globalQuery() { return search ? (search.value || "").trim().toLowerCase() : ""; }

  function rowsOf(pane) { return pane.querySelectorAll("tbody tr"); }

  function countForGroup(pane, code) {
    var n = 0;
    rowsOf(pane).forEach(function (tr) { if (tr.dataset.group === code) n++; });
    return n;
  }

  // ---- Age-classification measure (tracks the selected asset group) --------
  function classifyOf(tr) {
    var c = (tr.dataset.cls || "").toUpperCase();
    return (c === "G" || c === "Y" || c === "R") ? c : "U";
  }
  // One decimal, drop a trailing ".0" — matches the server's pct labels.
  function formatPct(p) { return p.toFixed(1).replace(/\.0$/, "") + "%"; }
  // Tally G/Y/R/Unknown across both tabs for the selected `code`. With no group
  // selected the chart shows nothing (all zeros), not a whole-table overview.
  function countByClass(code) {
    var counts = { G: 0, Y: 0, R: 0, U: 0 };
    if (!code) return counts;
    ["complete", "incomplete"].forEach(function (name) {
      rowsOf(panes[name]).forEach(function (tr) {
        if (tr.dataset.group === code) counts[classifyOf(tr)]++;
      });
    });
    return counts;
  }
  // Recompute the Age Classification donut + legend for `code`.
  function updateMeasure(code) {
    var counts = countByClass(code);
    var total = counts.G + counts.Y + counts.R + counts.U;

    // Legend: count, percentage, and a dimmed state for empty buckets.
    document.querySelectorAll(".lc-legend-item").forEach(function (item) {
      var key = item.getAttribute("data-bucket");
      if (!key) return;
      var n = counts[key] || 0;
      var pct = total ? (n * 100 / total) : 0;
      var cnt = item.querySelector(".lc-legend-count");
      var pctEl = item.querySelector(".lc-legend-pct");
      if (cnt) cnt.textContent = String(n);
      if (pctEl) pctEl.textContent = formatPct(pct);
      item.classList.toggle("is-empty", n === 0);
    });

    // Center label shows the running total for the selected group.
    var totalEl = document.querySelector("[data-donut-total]");
    if (totalEl) totalEl.textContent = String(total);

    // Donut segments. The ring's circumference is 100 (r = 15.9155), so each
    // bucket's stroke-dasharray is simply "<pct> <100-pct>". Segments chain
    // clockwise from 12 o'clock: offset = 125 - (cumulative pct before it).
    var acc = 0;
    var pending = [];
    document.querySelectorAll(".lc-donut-seg").forEach(function (seg) {
      var key = seg.getAttribute("data-bucket");
      if (!key) return;
      var n = counts[key] || 0;
      var pct = total ? (n * 100 / total) : 0;
      pending.push([seg, pct, acc]);
      acc += pct;
    });
    requestAnimationFrame(function () {
      pending.forEach(function (u) {
        var seg = u[0], pct = u[1], before = u[2];
        seg.setAttribute("stroke-dasharray", pct + " " + (100 - pct));
        seg.setAttribute("stroke-dashoffset", String(125 - before));
      });
    });

    // Companion bar chart: expiring assets (>= 10 yrs) by year + Unknown.
    renderAgeChart(code);
  }

  // ---- Life-cycle-expiry-by-year bar chart (sibling of the donut) ----------
  // X = expiry year = installation year + 10 (the year the asset reaches 10 yrs
  // of service), Y = years in service (age). Each bar rises to that cohort's age;
  // a horizontal dashed line at 10 yrs marks the expiry threshold, so bars at/above
  // it (red) have already expired or are expiring now, and bars below it (green/
  // amber) expire in a future year. The count of assets is labelled on each bar.
  // Rows with no installation date have no expiry year, so they're omitted here
  // (the donut still reports them as Unknown). Scoped to `code`.
  function ageDataFor(code) {
    var byYear = {};
    if (code) {
      ["complete", "incomplete"].forEach(function (name) {
        rowsOf(panes[name]).forEach(function (tr) {
          if (tr.dataset.group !== code) return;
          var iy = parseInt(tr.dataset.installYear, 10);
          var yrs = parseFloat(tr.dataset.years);
          if (isNaN(iy) || isNaN(yrs)) return;
          var g = byYear[iy] || (byYear[iy] = { count: 0, sum: 0 });
          g.count += 1; g.sum += yrs;
        });
      });
    }
    var cols = Object.keys(byYear).map(Number).sort(function (a, b) { return a - b; })
      .map(function (y) {
        var g = byYear[y];
        // Label by expiry year (install + 10); height stays the current age.
        return { year: y + 10, count: g.count, age: g.sum / g.count };
      });
    var maxAge = 0;
    cols.forEach(function (c) { if (c.age > maxAge) maxAge = c.age; });
    return { cols: cols, maxAge: maxAge };
  }

  // Age band -> donut-matching tone (years <= 8 good, 8-10 caution, >= 10 critical).
  function ageBand(age) { return age >= 10 ? "critical" : (age > 8 ? "caution" : "good"); }

  function renderAgeChart(code) {
    var host = document.getElementById("age-chart");
    if (!host) return;
    var data = ageDataFor(code);
    if (!data.cols.length) {
      host.innerHTML = '<span class="lc-agechart-emptymsg">' +
        (code ? "No dated assets to project" : "Select a group to see life-cycle expiry") + "</span>";
      return;
    }

    // Y scale: 0..top in 10-yr ticks, at least 0–30, with headroom above the
    // oldest cohort so its count label clears the top.
    var top = Math.max(30, Math.ceil((data.maxAge + 3) / 10) * 10);
    var ticks = [];
    for (var t = 0; t <= top; t += 10) ticks.push(t);

    var yaxis = ticks.map(function (t) {
      return '<span class="lc-agechart-ytick" style="bottom:' + (t / top * 100) + '%">' + t + "</span>";
    }).join("");

    // Horizontal gridlines; the 10-yr one is dashed, labelled, and drawn on top.
    var lines = ticks.filter(function (t) { return t > 0; }).map(function (t) {
      var ten = (t === 10);
      return '<div class="lc-agechart-line' + (ten ? " lc-agechart-line--ten" : "") +
        '" style="bottom:' + (t / top * 100) + '%">' +
        (ten ? '<span class="lc-agechart-linelabel">10&nbsp;yr</span>' : "") + "</div>";
    }).join("");

    var bars = data.cols.map(function (c) {
      var h = (c.age / top) * 100;
      return '<div class="lc-agechart-col" title="' + c.count + " asset" +
        (c.count === 1 ? "" : "s") + " reach 10 yrs in " + c.year + " (installed " +
        (c.year - 10) + ") · ~" + c.age.toFixed(1) + ' yrs in service now">' +
        '<span class="lc-agechart-count">' + c.count + "</span>" +
        '<span class="lc-agechart-bar lc-agechart-bar--' + ageBand(c.age) +
          '" data-h="' + h + '"></span></div>';
    }).join("");

    var xcells = data.cols.map(function (c) {
      return '<span class="lc-agechart-xcell">' + c.year + "</span>";
    }).join("");

    host.innerHTML =
      '<div class="lc-agechart-body">' +
        '<div class="lc-agechart-yaxis">' + yaxis + "</div>" +
        '<div class="lc-agechart-plot">' +
          '<div class="lc-agechart-track">' + lines +
            '<div class="lc-agechart-bars">' + bars + "</div>" +
          "</div>" +
          '<div class="lc-agechart-xrow">' + xcells + "</div>" +
        "</div>" +
      "</div>";

    var els = host.querySelectorAll(".lc-agechart-bar");
    requestAnimationFrame(function () {
      els.forEach(function (el) { el.style.height = el.getAttribute("data-h") + "%"; });
    });
  }

  // ---- Row matching (shared by table filtering and faceting) ---------------
  function rowMatches(tr, code, f) {
    if (tr.dataset.group !== code) return false;
    if (f.query && tr.textContent.toLowerCase().indexOf(f.query) === -1) return false;
    if (f.cls && tr.dataset.cls !== f.cls) return false;
    if (f.qr && (tr.dataset.qr || "").indexOf(f.qr) === -1) return false;
    if (f.tag && (tr.dataset.tag || "").indexOf(f.tag) === -1) return false;
    // Building filter is multi-select: keep rows in ANY selected building (OR).
    if (f.building && f.building.length && f.building.indexOf(tr.dataset.building) === -1) return false;
    // Capture status (Incomplete tab): "yes" / "no" exact match.
    if (f.capture && (tr.dataset.capture || "") !== f.capture) return false;
    // Capture Date: exact day match (cell is date-only YYYY-MM-DD).
    if (f.captureDate && (tr.dataset.captureDate || "") !== f.captureDate) return false;
    return true;
  }

  function paneRowsMatching(paneName, code, f) {
    var out = [];
    rowsOf(panes[paneName]).forEach(function (tr) {
      if (rowMatches(tr, code, f)) out.push(tr);
    });
    return out;
  }

  // ---- Table filtering -----------------------------------------------------
  function filterPane(pane, code, f) {
    var visible = 0;
    rowsOf(pane).forEach(function (tr) {
      var show = rowMatches(tr, code, f);
      tr.style.display = show ? "" : "none";
      if (show) {
        // Zebra-stripe the visible rows in order (like DataTables).
        tr.classList.toggle("row-alt", (visible % 2) === 1);
        visible++;
      } else {
        tr.classList.remove("row-alt");
      }
    });
    var noRows = pane.querySelector(".no-rows");
    if (noRows) noRows.hidden = visible > 0;
    return visible;
  }

  function currentCode() { return select.value; }

  // The active filter set for a pane (global search + that tab's controls).
  function currentFilterSet(paneName) {
    var ctrl = controls[paneName];
    var f = { query: globalQuery() };
    if (ctrl.qr) f.qr = inputVal(ctrl.qr);
    if (ctrl.tag) f.tag = inputVal(ctrl.tag);
    if (ctrl.building) { var bs = ctrl.building.values(); if (bs.length) f.building = bs; }
    if (ctrl.classFilter) f.cls = ctrl.classFilter;
    if (ctrl.capture && ctrl.capture.value) f.capture = ctrl.capture.value;
    if (ctrl.captureDate && ctrl.captureDate.value) f.captureDate = ctrl.captureDate.value;
    return f;
  }

  function refreshActivePane() {
    var code = currentCode();
    if (!code) return;
    filterPane(panes[activeTab], code, currentFilterSet(activeTab));
  }

  // ---- Faceting: each filter's options reflect the others ------------------
  function tallyCls(rows) {
    var t = { G: 0, Y: 0, R: 0 };
    rows.forEach(function (tr) {
      var c = tr.dataset.cls;
      if (c && Object.prototype.hasOwnProperty.call(t, c)) t[c]++;
    });
    return t;
  }

  function renderChart(ctrl, t) {
    if (!ctrl.chart) return;
    var max = Math.max(t.G, t.Y, t.R, 1);
    ctrl.chartBars.forEach(function (bar) {
      var c = bar.dataset.cls;
      var n = t[c] || 0;
      bar.querySelector(".chart-count").textContent = n;
      bar.querySelector(".chart-fill").style.height = (n / max * 100) + "%";
      bar.disabled = n === 0;
    });
  }

  function syncChartActive(ctrl) {
    if (!ctrl.chart) return;
    ctrl.chartBars.forEach(function (bar) {
      var on = ctrl.classFilter !== null && bar.dataset.cls === ctrl.classFilter;
      bar.classList.toggle("is-active", on);
      bar.setAttribute("aria-pressed", on ? "true" : "false");
    });
    ctrl.chart.classList.toggle("has-filter", ctrl.classFilter !== null);
  }

  // Rebuild the faceted building list (unique names across the rows matching
  // the OTHER filters) and hand it to the multi-select. Returns true if the
  // selection self-healed (a checked building dropped out of the new list).
  function renderBuildingOptions(buildingComp, paneName, code, f) {
    if (!buildingComp) return false;
    var seen = {};
    var list = [];
    paneRowsMatching(paneName, code, f).forEach(function (tr) {
      var b = tr.dataset.building;
      if (b && !seen[b]) { seen[b] = true; list.push(b); }
    });
    list.sort();
    return buildingComp.setOptions(list);
  }

  // Reconcile a tab's chart counts and Building options against its other
  // filters. Self-heals selections that drop to zero rows.
  function refreshFacets(paneName) {
    var code = currentCode();
    if (!code) return;
    var ctrl = controls[paneName];
    var query = globalQuery();
    var qr = inputVal(ctrl.qr);
    var tag = inputVal(ctrl.tag);
    var buildingVals = ctrl.building ? ctrl.building.values() : [];

    if (ctrl.chart) {
      // Capture status + Capture Date also constrain this tab's facets.
      var capture = ctrl.capture ? ctrl.capture.value : "";
      var captureDate = ctrl.captureDate ? ctrl.captureDate.value : "";
      // Chart counts: every filter EXCEPT classification (incl. building + capture).
      var tally = tallyCls(paneRowsMatching(paneName, code, {
        query: query, qr: qr, tag: tag, building: buildingVals.length ? buildingVals : undefined,
        capture: capture || undefined, captureDate: captureDate || undefined,
      }));
      if (ctrl.classFilter && (tally[ctrl.classFilter] || 0) === 0) ctrl.classFilter = null;
      // Building options: every filter EXCEPT building (incl. finalized cls + capture).
      var healed = renderBuildingOptions(ctrl.building, paneName, code, {
        query: query, qr: qr, tag: tag, cls: ctrl.classFilter,
        capture: capture || undefined, captureDate: captureDate || undefined,
      });
      if (healed) {
        var bv = ctrl.building.values();
        tally = tallyCls(paneRowsMatching(paneName, code, {
          query: query, qr: qr, tag: tag, building: bv.length ? bv : undefined,
          capture: capture || undefined, captureDate: captureDate || undefined,
        }));
      }
      renderChart(ctrl, tally);
      syncChartActive(ctrl);
    } else {
      // Incomplete tab: Building options reflect the text and capture filters.
      var capture = ctrl.capture ? ctrl.capture.value : "";
      var captureDate = ctrl.captureDate ? ctrl.captureDate.value : "";
      renderBuildingOptions(ctrl.building, paneName, code, {
        query: query, qr: qr, tag: tag,
        capture: capture || undefined, captureDate: captureDate || undefined,
      });
    }
  }

  // ---- Sorting -------------------------------------------------------------
  function parseDate(s) {
    var m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s); // dd/mm/yyyy
    if (m) return new Date(+m[3], +m[2] - 1, +m[1]).getTime();
    var d = Date.parse(s);
    return isNaN(d) ? null : d;
  }

  function cellVal(tr, idx, type) {
    var td = tr.children[idx];
    if (!td) return null;
    var raw = td.getAttribute("data-sort");
    if (raw === null) raw = td.textContent.trim();
    if (raw === "" || raw === "—") return null;
    if (type === "number" || type === "class") {
      var n = parseFloat(raw);
      return isNaN(n) ? null : n;
    }
    if (type === "date") return parseDate(raw);
    return raw.toLowerCase();
  }

  function headerCells(pane) {
    var table = pane.querySelector("table");
    return table && table.tHead ? table.tHead.rows[0].cells : [];
  }

  function sortPane(pane, key, dir) {
    var ths = headerCells(pane);
    var idx = -1, type = "text";
    for (var i = 0; i < ths.length; i++) {
      if (ths[i].getAttribute("data-key") === key) {
        idx = i;
        type = ths[i].getAttribute("data-type") || "text";
        break;
      }
    }
    if (idx < 0) return;

    var tbody = pane.querySelector("tbody");
    var rows = Array.prototype.slice.call(tbody.rows);
    var mul = dir === "desc" ? -1 : 1;
    rows.sort(function (a, b) {
      var va = cellVal(a, idx, type);
      var vb = cellVal(b, idx, type);
      if (va === null && vb === null) return 0;
      if (va === null) return 1;          // blanks last, regardless of direction
      if (vb === null) return -1;
      if (va < vb) return -1 * mul;
      if (va > vb) return 1 * mul;
      return 0;
    });
    var frag = document.createDocumentFragment();
    rows.forEach(function (r) { frag.appendChild(r); });
    tbody.appendChild(frag);

    for (var j = 0; j < ths.length; j++) {
      if (ths[j].getAttribute("data-key") === key) {
        ths[j].setAttribute("aria-sort", dir === "desc" ? "descending" : "ascending");
      } else {
        ths[j].removeAttribute("aria-sort");
      }
    }
  }

  function applySort(paneName) {
    var s = sortState[paneName];
    sortPane(panes[paneName], s.key, s.dir);
  }

  function wireSortHeaders(paneName) {
    var pane = panes[paneName];
    pane.querySelectorAll("thead th.sortable").forEach(function (th) {
      function trigger() {
        var key = th.getAttribute("data-key");
        var cur = sortState[paneName];
        var dir;
        if (cur.key === key) {
          dir = cur.dir === "asc" ? "desc" : "asc";
        } else {
          dir = th.getAttribute("data-type") === "text" ? "asc" : "desc";
        }
        sortState[paneName] = { key: key, dir: dir };
        sortPane(pane, key, dir);
        refreshActivePane();   // re-stripe visible rows in the new order
      }
      th.addEventListener("click", trigger);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); trigger(); }
      });
    });
  }

  // ---- Filter change orchestration ----------------------------------------
  function onFilterChange(paneName) {
    var code = currentCode();
    if (!code) return;
    refreshFacets(paneName);
    filterPane(panes[paneName], code, currentFilterSet(paneName));
  }

  function wireControls() {
    ["complete", "incomplete"].forEach(function (paneName) {
      var ctrl = controls[paneName];
      if (ctrl.qr) ctrl.qr.addEventListener("input", function () { onFilterChange(paneName); });
      if (ctrl.tag) ctrl.tag.addEventListener("input", function () { onFilterChange(paneName); });
      if (ctrl.building) ctrl.building.onChange(function () { onFilterChange(paneName); });
      if (ctrl.capture) ctrl.capture.addEventListener("change", function () { onFilterChange(paneName); });
      if (ctrl.captureDate) ctrl.captureDate.addEventListener("change", function () { onFilterChange(paneName); });
      ctrl.chartBars.forEach(function (bar) {
        bar.addEventListener("click", function () {
          if (bar.disabled) return;
          var c = bar.dataset.cls;
          ctrl.classFilter = ctrl.classFilter === c ? null : c;
          onFilterChange(paneName);
        });
      });
    });
  }

  // ---- Tabs / group --------------------------------------------------------
  function setTab(name) {
    activeTab = name;
    Object.keys(tabs).forEach(function (key) {
      var isActive = key === name;
      tabs[key].classList.toggle("active", isActive);
      tabs[key].setAttribute("aria-selected", isActive ? "true" : "false");
      panes[key].classList.toggle("active", isActive);
      panes[key].hidden = !isActive;
    });
    tabHint.textContent = HINTS[name];
    refreshActivePane();
  }

  function clearFilters() {
    if (search) search.value = "";
    ["complete", "incomplete"].forEach(function (paneName) {
      var ctrl = controls[paneName];
      if (ctrl.qr) ctrl.qr.value = "";
      if (ctrl.tag) ctrl.tag.value = "";
      if (ctrl.building) ctrl.building.clear();
      if (ctrl.capture) ctrl.capture.value = "";
      if (ctrl.captureDate) ctrl.captureDate.value = "";
      ctrl.classFilter = null;
    });
  }

  function applyGroup(code) {
    // The Age Classification measure tracks the selected group (whole portfolio
    // when nothing is selected yet).
    updateMeasure(code);
    if (!code) {
      results.hidden = true;
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;
    results.hidden = false;

    var label = select.options[select.selectedIndex].text.split(" — ")[0];
    groupTitle.textContent = label;

    counts.complete.textContent = countForGroup(panes.complete, code);
    counts.incomplete.textContent = countForGroup(panes.incomplete, code);

    // Fresh group: clear filters, rebuild both tabs' facets, restore sort.
    clearFilters();
    refreshFacets("complete");
    refreshFacets("incomplete");
    sortState.complete = cloneSort(DEFAULT_SORT);
    sortState.incomplete = cloneSort(DEFAULT_SORT);
    applySort("complete");
    applySort("incomplete");

    setTab("complete");
  }

  function resetView() {
    var code = currentCode();
    if (!code) return;
    clearFilters();
    refreshFacets("complete");
    refreshFacets("incomplete");
    sortState.complete = cloneSort(DEFAULT_SORT);
    sortState.incomplete = cloneSort(DEFAULT_SORT);
    applySort("complete");
    applySort("incomplete");
    refreshActivePane();
  }

  // ---- Listeners -----------------------------------------------------------
  select.addEventListener("change", function () {
    applyGroup(currentCode());
  });

  Object.keys(tabs).forEach(function (key) {
    tabs[key].addEventListener("click", function () {
      setTab(key);
    });
  });

  if (search) search.addEventListener("input", function () { onFilterChange(activeTab); });

  if (resetBtn) resetBtn.addEventListener("click", resetView);

  // ---- Excel export of the active tab's currently visible rows ------------
  function exportXlsx() {
    var code = currentCode();
    if (!code || !exportBtn) return;
    var qrCodes = [];
    rowsOf(panes[activeTab]).forEach(function (tr) {
      if (tr.style.display === "none") return;          // respect active filters
      var qrCell = tr.querySelector("td.mono");          // QR Code, in display order
      if (qrCell) qrCodes.push(qrCell.textContent.trim());
    });
    if (!qrCodes.length) return;

    exportBtn.disabled = true;
    // Build the endpoint from the current path so it resolves whether or not
    // the page URL has a trailing slash (e.g. /life-cycle/ vs /life-cycle).
    var base = window.location.pathname.replace(/\/?$/, "/");
    fetch(base + "export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qr_codes: qrCodes, tab: activeTab, group_code: code }),
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("export failed: " + resp.status);
        var disp = resp.headers.get("Content-Disposition") || "";
        var m = /filename="([^"]+)"/.exec(disp);
        return resp.blob().then(function (blob) {
          return { blob: blob, name: m ? m[1] : "life_cycle_export.xlsx" };
        });
      })
      .then(function (out) {
        var url = URL.createObjectURL(out.blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = out.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch(function (e) { console.error(e); })
      .then(function () { exportBtn.disabled = false; });
  }

  if (exportBtn) exportBtn.addEventListener("click", exportXlsx);

  // ---- Run the data-source -> PostgreSQL interface (header button) ---------
  // The button opens the OS file picker; the chosen workbook is uploaded to
  // /refresh, which rebuilds the life_cycle table. On success we reload so the
  // fresh data shows, keeping the user on their current group and tab.
  var refreshBtn = document.getElementById("refresh-btn");
  var refreshFile = document.getElementById("refresh-file");
  var refreshStatus = document.getElementById("refresh-status");
  var refreshDialog = document.getElementById("refresh-dialog");
  var dlgIcon = document.getElementById("refresh-dialog-icon");
  var dlgTitle = document.getElementById("refresh-dialog-title");
  var dlgBody = document.getElementById("refresh-dialog-body");
  var dlgOk = document.getElementById("refresh-dialog-ok");
  var reloadAfterDialog = false;

  function setRefreshStatus(text, kind) {
    if (!refreshStatus) return;
    refreshStatus.textContent = text || "";
    refreshStatus.className = "refresh-status" + (kind ? " is-" + kind : "");
  }

  function reloadKeepingPlace() {
    // Preserve the selected group/tab through the reload via the deep-link
    // params initFromUrl() already understands.
    var code = currentCode();
    var params = new URLSearchParams();
    if (code) { params.set("group", code); params.set("tab", activeTab); }
    // Keep ?embedded=true so the header chrome stays hidden inside the iframe
    // after a successful "Update Database" reload.
    if (new URLSearchParams(window.location.search).get("embedded") === "true") {
      params.set("embedded", "true");
    }
    var qs = params.toString();
    window.location.href = window.location.pathname + (qs ? "?" + qs : "");
  }

  // Append a label/value pair to the summary definition list.
  function addSummaryRow(dl, label, value) {
    var dt = document.createElement("dt"); dt.textContent = label;
    var dd = document.createElement("dd"); dd.textContent = value;
    dl.appendChild(dt); dl.appendChild(dd);
  }

  function openDialog() {
    if (refreshDialog && typeof refreshDialog.showModal === "function") {
      refreshDialog.showModal();
    } else if (reloadAfterDialog) {
      reloadKeepingPlace();            // no <dialog> support: just go straight to fresh data
    } else {
      window.alert(dlgBody.textContent);
    }
  }

  // Success: show the process summary; reload to the fresh data on dismiss.
  function showSummaryDialog(out) {
    reloadAfterDialog = true;
    if (dlgIcon) { dlgIcon.textContent = "✓"; dlgIcon.className = "lc-dialog-icon is-ok"; }
    if (dlgTitle) dlgTitle.textContent = "Database updated";
    if (dlgBody) {
      dlgBody.innerHTML = "";
      var dl = document.createElement("dl");
      dl.className = "lc-summary";
      addSummaryRow(dl, "Source file", out.filename || "—");
      addSummaryRow(dl, "Rows loaded", String(out.rows));
      addSummaryRow(dl, "Columns", String(out.columns));
      if (out.ref_rows != null) addSummaryRow(dl, "Reference table (space_floor)", out.ref_rows + " rows");
      addSummaryRow(dl, "Foreign key", out.fk_ok ? "Present" : "Missing");
      if (out.last_loaded) addSummaryRow(dl, "Last updated", out.last_loaded);
      dlgBody.appendChild(dl);
    }
    if (dlgOk) dlgOk.textContent = "View updated data";
    openDialog();
  }

  // Failure: show the error; stay on the current (unchanged) data on dismiss.
  function showErrorDialog(message) {
    reloadAfterDialog = false;
    if (dlgIcon) { dlgIcon.textContent = "!"; dlgIcon.className = "lc-dialog-icon is-error"; }
    if (dlgTitle) dlgTitle.textContent = "Update failed";
    if (dlgBody) {
      dlgBody.innerHTML = "";
      var p = document.createElement("p");
      p.className = "lc-dialog-msg";
      p.textContent = message;
      dlgBody.appendChild(p);
    }
    if (dlgOk) dlgOk.textContent = "Close";
    openDialog();
  }

  if (refreshDialog) {
    refreshDialog.addEventListener("close", function () {
      if (reloadAfterDialog) reloadKeepingPlace();
    });
  }

  function runRefresh() {
    if (!refreshFile || !refreshFile.files || !refreshFile.files.length) return;
    var file = refreshFile.files[0];
    var data = new FormData();
    data.append("file", file);

    if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.classList.add("is-busy"); }
    setRefreshStatus("Loading " + file.name + "…", "busy");

    var base = window.location.pathname.replace(/\/?$/, "/");
    fetch(base + "refresh", { method: "POST", body: data })
      .then(function (resp) {
        return resp.json().catch(function () { return { ok: false, error: "Server error " + resp.status }; });
      })
      .then(function (out) {
        if (!out || !out.ok) throw new Error(out && out.error ? out.error : "Refresh failed.");
        showSummaryDialog(out);
      })
      .catch(function (e) {
        showErrorDialog(String(e.message || e));
      })
      .then(function () {
        // Loading is done: stop the spinner, clear the transient status, and
        // reset the input so picking the same file again re-fires "change".
        if (refreshBtn) { refreshBtn.disabled = false; refreshBtn.classList.remove("is-busy"); }
        setRefreshStatus("");
        if (refreshFile) refreshFile.value = "";
      });
  }

  if (refreshBtn && refreshFile) {
    refreshBtn.addEventListener("click", function () { refreshFile.click(); });
    refreshFile.addEventListener("change", runRefresh);
  }

  wireControls();
  wireSortHeaders("complete");
  wireSortHeaders("incomplete");

  // The Age Classification measure is filled by updateMeasure(), invoked from
  // applyGroup() on load and whenever the asset-group dropdown changes.

  // ---- Dark mode toggle (light-only build: button removed, guard kept) ----
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var current = root.getAttribute("data-theme");
      var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      var isDark = current === "dark" || (!current && prefersDark);
      var next = isDark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("lc-theme", next); } catch (e) {}
    });
  }

  // Optional deep-linking: ?group=<code>&tab=complete|incomplete
  function initFromUrl() {
    var wantTab = null;
    var appliedGroup = false;
    try {
      var p = new URLSearchParams(window.location.search);
      var g = p.get("group");
      if (g) {
        for (var i = 0; i < select.options.length; i++) {
          if (select.options[i].value === g) { select.value = g; appliedGroup = true; break; }
        }
      }
      var t = p.get("tab");
      if (t === "complete" || t === "incomplete") wantTab = t;
    } catch (e) {}
    // With no ?group in the URL, always start on the "Choose an asset group"
    // placeholder, overriding any value the browser restored from the previous
    // page on refresh (form restoration).
    if (!appliedGroup) select.value = "";
    applyGroup(currentCode());
    if (wantTab && currentCode()) setTab(wantTab);
  }

  initFromUrl();
})();
