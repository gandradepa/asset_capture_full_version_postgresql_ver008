/* building-multiselect.js — checkbox dropdown with type-to-filter search for the
   review dashboards' Building filter, adapted from the Life Cycle dashboard's
   makeBuildingSelect() (Dashboard/life_cycle/static/js/dashboard.js).

   FOUR-COPY RULE: this file exists byte-identically in the ME, BF, and EL review
   apps and in Dashboard/static/ (the FLS Devices Property filter). Edit all four
   together; `git diff --no-index` between copies must stay empty.

   Markup contract (the root element passed to create()):
     <div class="ms">
       <button type="button" class="ms-toggle" aria-expanded="false">
         <span class="ms-text">All Buildings</span><span class="ms-caret">▾</span>
       </button>
       <div class="ms-panel" hidden>
         <input type="search" class="ms-search">
         <div class="ms-actions">
           <button type="button" class="ms-action" data-act="all">Select all</button>
           <button type="button" class="ms-action" data-act="none">Clear</button>
         </div>
         <div class="ms-options"><!-- checkbox rows injected here --></div>
       </div>
     </div>

   API:
     var ms = BuildingMultiselect.create(rootEl, { allLabel: 'All Buildings' });
     // opts.single: true -> single-select mode (EL): checking a building replaces
     // the selection and closes the panel (committing via onClose); "Select all"
     // is hidden; setValues() keeps only the first code. Default: multi-select.
     // opts.emptyLabel: text of the "no options" row (default "No buildings").
     ms.values()         -> sorted array of selected codes; [] means "no filter"
     ms.setOptions(list) -> list = [{value, label}]; rebuilds rows, keeps checks
                            for values still offered, drops the rest (self-heal).
                            Returns true when the selection set changed.
     ms.setValues(codes) -> replace the selection (intersected with the options)
     ms.clear()          -> uncheck everything, reset search + label
     ms.onChange(fn)     -> fire fn after any selection change
     ms.onClose(fn)      -> fire fn(changed) when the panel closes; changed is
                            true when the selection differs from panel-open time
     BuildingMultiselect.buildColumnRegex(['A','B']) -> '^(A|B)$'   ([] -> '')
     BuildingMultiselect.parseColumnRegex('^(A|B)$') -> ['A','B']   (also legacy '^A$')
*/
(function () {
  "use strict";

  // DataTables-compatible escape set (includes "-", which building codes use).
  var ESCAPE_RE = /[.*+?|()\[\]{}\\$^\-]/g;
  function escapeRegex(s) { return String(s).replace(ESCAPE_RE, "\\$&"); }
  function unescapeRegex(s) { return String(s).replace(/\\(.)/g, "$1"); }

  function buildColumnRegex(values) {
    if (!values || !values.length) return "";
    var parts = [];
    for (var i = 0; i < values.length; i++) parts.push(escapeRegex(values[i]));
    return "^(" + parts.join("|") + ")$";
  }

  function parseColumnRegex(str) {
    var s = String(str || "");
    if (!s) return [];
    if (s.charAt(0) !== "^" || s.charAt(s.length - 1) !== "$") return [unescapeRegex(s)];
    s = s.slice(1, -1);
    // New OR form is wrapped in bare parens; a legacy single escaped value never
    // starts with a bare "(" (it would be "\(").
    if (s.charAt(0) === "(" && s.charAt(s.length - 1) === ")") s = s.slice(1, -1);
    if (!s) return [];
    var parts = [], cur = "";
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (ch === "\\" && i + 1 < s.length) { cur += ch + s.charAt(i + 1); i++; continue; }
      if (ch === "|") { parts.push(cur); cur = ""; continue; }
      cur += ch;
    }
    parts.push(cur);
    var out = [];
    for (var j = 0; j < parts.length; j++) {
      var v = unescapeRegex(parts[j]);
      if (v !== "") out.push(v);
    }
    return out;
  }

  // Selection lives in a value->true map so it survives the option-list rebuilds
  // that faceted redraws trigger. Contract mirrors the Life Cycle component.
  function create(root, opts) {
    if (!root) return null;
    opts = opts || {};
    var allLabel = opts.allLabel || "All Buildings";
    var emptyLabel = opts.emptyLabel || "No buildings";
    var singleMode = !!opts.single;
    var toggle = root.querySelector(".ms-toggle");
    var textEl = root.querySelector(".ms-text");
    var panel = root.querySelector(".ms-panel");
    var searchEl = root.querySelector(".ms-search");
    var optionsEl = root.querySelector(".ms-options");
    var selected = {};          // value -> true (the checked set)
    var labels = {};            // value -> label (for the single-selection caption)
    var available = [];         // [{value, label}] currently offered
    var changeCb = null;
    var closeCb = null;
    var openSnapshot = null;    // values() joined at panel-open time, for onClose(changed)

    function has(map, key) { return Object.prototype.hasOwnProperty.call(map, key); }
    function selectedValues() { return Object.keys(selected).sort(); }

    function syncLabel() {
      var vals = selectedValues();
      if (vals.length === 0) textEl.textContent = allLabel;
      else if (vals.length === 1) textEl.textContent = labels[vals[0]] || vals[0];
      else textEl.textContent = vals.length + " selected";
    }
    function applySearch() {
      var q = ((searchEl && searchEl.value) || "").trim().toLowerCase();
      optionsEl.querySelectorAll(".ms-option").forEach(function (row) {
        var label = row.dataset.label || "";
        row.hidden = q !== "" && label.toLowerCase().indexOf(q) === -1;
      });
    }
    function buildRows() {
      optionsEl.innerHTML = "";
      if (!available.length) {
        var empty = document.createElement("div");
        empty.className = "ms-empty";
        empty.textContent = emptyLabel;
        optionsEl.appendChild(empty);
        return;
      }
      available.forEach(function (opt) {
        var row = document.createElement("label");
        row.className = "ms-option";
        row.dataset.value = opt.value;
        row.dataset.label = opt.label;
        row.title = opt.label;   // full text on hover (long labels ellipsize)
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = opt.value;
        cb.checked = has(selected, opt.value);
        row.classList.toggle("is-on", cb.checked);
        var span = document.createElement("span");
        span.className = "ms-option-name";
        span.textContent = opt.label;
        row.appendChild(cb);
        row.appendChild(span);
        optionsEl.appendChild(row);
        cb.addEventListener("change", function () {
          if (singleMode) {
            // Radio-like: checking a building replaces the selection; unchecking
            // clears it. Committing happens on the auto-close below.
            selected = {};
            if (cb.checked) selected[opt.value] = true;
            buildRows();
          } else {
            if (cb.checked) selected[opt.value] = true; else delete selected[opt.value];
            row.classList.toggle("is-on", cb.checked);
          }
          syncLabel();
          if (changeCb) changeCb();
          if (singleMode) closePanel();
        });
      });
      applySearch();
    }

    function setOptions(list) {
      available = (list || []).map(function (o) {
        var value = String(o.value);
        return { value: value, label: o.label == null ? value : String(o.label) };
      });
      labels = {};
      var present = {};
      available.forEach(function (o) { present[o.value] = true; labels[o.value] = o.label; });
      // Self-heal: drop checked values that are no longer offered.
      var changed = false;
      Object.keys(selected).forEach(function (v) {
        if (!has(present, v)) { delete selected[v]; changed = true; }
      });
      buildRows();
      syncLabel();
      return changed;
    }
    function setValues(codes) {
      selected = {};
      (codes || []).forEach(function (c) {
        c = String(c == null ? "" : c).trim();
        if (!c || !has(labels, c)) return;
        if (singleMode && Object.keys(selected).length) return; // keep first only
        selected[c] = true;
      });
      buildRows();
      syncLabel();
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
      openSnapshot = selectedValues().join(",");
      panel.hidden = false;
      positionPanel();
      toggle.setAttribute("aria-expanded", "true");
      root.classList.add("is-open");
    }
    function closePanel() {
      if (panel.hidden) return;
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      root.classList.remove("is-open");
      var changed = openSnapshot !== null && openSnapshot !== selectedValues().join(",");
      openSnapshot = null;
      if (closeCb) closeCb(changed);
    }
    function togglePanel() { if (panel.hidden) openPanel(); else closePanel(); }
    // A fixed panel must track the toggle as the page/table scrolls or resizes.
    function reflow() { if (!panel.hidden) positionPanel(); }
    window.addEventListener("scroll", reflow, true);
    window.addEventListener("resize", reflow);

    toggle.addEventListener("click", function (e) { e.stopPropagation(); togglePanel(); });
    panel.addEventListener("click", function (e) { e.stopPropagation(); });
    if (searchEl) searchEl.addEventListener("input", applySearch);
    root.querySelectorAll(".ms-action").forEach(function (btn) {
      if (singleMode && btn.dataset.act === "all") { btn.hidden = true; return; }
      btn.addEventListener("click", function () {
        var act = btn.dataset.act;
        if (act === "all") available.forEach(function (o) { selected[o.value] = true; });
        else selected = {};
        buildRows();
        syncLabel();
        if (changeCb) changeCb();
        if (singleMode) closePanel();
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
      values: selectedValues,
      setOptions: setOptions,
      setValues: setValues,
      clear: clear,
      onChange: function (fn) { changeCb = fn; },
      onClose: function (fn) { closeCb = fn; },
    };
  }

  window.BuildingMultiselect = {
    create: create,
    buildColumnRegex: buildColumnRegex,
    parseColumnRegex: parseColumnRegex,
  };
})();
