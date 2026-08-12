/* ============================================================
   UBC Asset Management Shell — sidebar + topbar injector
   - Pure JS, no dependencies
   - Idempotent (safe to load multiple times)
   - Does NOT modify any existing DOM nodes; only appends shell
   - Reads active app from <meta name="acshell-active">
   - Skips injection when running inside an iframe (embedded mode)
   ============================================================ */
(function () {
  'use strict';

  // ---- Configuration ----
  var APPS = {
    'dashboard':  { breadcrumb: ['Home', 'Dashboard'],            label: 'Dashboard' },
    'me-review':  { breadcrumb: ['Home', 'Asset Review', 'Mechanical Reviewer'], label: 'ME Reviewer' },
    'bf-review':  { breadcrumb: ['Home', 'Asset Review', 'Backflow Reviewer'],   label: 'BF Reviewer' },
    'el-review':  { breadcrumb: ['Home', 'Asset Review', 'Electrical Reviewer'], label: 'EL Reviewer' },
    'sdi':        { breadcrumb: ['Home', 'SDI / Planon', 'SDI Process'],          label: 'SDI Process' },
    'life':       { breadcrumb: ['Home', 'Operations', 'Life Cycle Assessment'],  label: 'Life Cycle Assessment' }
  };

  // Per-app external URLs (production subdomains). Sub-app links open
  // the corresponding service. Localhost developers can override via the
  // window.AC_SHELL_URLS object if they want to point everything at
  // 127.0.0.1:<port>.
  var DEFAULT_URLS = {
    'dashboard': 'https://dashboardprod.assetcap.facilities.ubc.ca/',
    'me-review': 'https://reviewme.assetcap.facilities.ubc.ca/',
    'bf-review': 'https://reviewbf.assetcap.facilities.ubc.ca/',
    'el-review': 'https://reviewel.assetcap.facilities.ubc.ca/',
    'sdi':       'https://sdiprocess.assetcap.facilities.ubc.ca/',
    'capture':   'https://appprod.assetcap.facilities.ubc.ca/',
    'map':       'https://dashboardprod.assetcap.facilities.ubc.ca/map-new-assets',
    'dict':      'https://dashboardprod.assetcap.facilities.ubc.ca/dictionary',
    'logs':      'https://dashboardprod.assetcap.facilities.ubc.ca/#qr-pending',
    'kpis':      'https://dashboardprod.assetcap.facilities.ubc.ca/#analytics',
    'fls':       'https://dashboardprod.assetcap.facilities.ubc.ca/#fls-assets',
    'life':      'https://dashboardprod.assetcap.facilities.ubc.ca/life-cycle/',
    'disposed':  'https://dashboardprod.assetcap.facilities.ubc.ca/#disposed',
    'cost':      'https://dashboardprod.assetcap.facilities.ubc.ca/#operational-cost',
    'useract':   'https://dashboardprod.assetcap.facilities.ubc.ca/#user-activity',
    'admin':     'https://dashboardprod.assetcap.facilities.ubc.ca/#user-admin',
    'pwd':       'https://dashboardprod.assetcap.facilities.ubc.ca/change-password',
    'logout':    '/logout'
  };

  // ---- Icons (Lucide subset, inline SVG) ----
  var ICON = {
    'menu':       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
    'x':          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    'search':     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    'bell':       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>',
    'dashboard':  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>',
    'camera':     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>',
    'map':        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg>',
    'book':       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    'wrench':     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    'zap':        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    'droplets':   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 16.3c2.2 0 4-1.83 4-4.05 0-1.16-.57-2.26-1.71-3.19S7.29 6.75 7 5.3c-.29 1.45-1.14 2.84-2.29 3.76S3 11.1 3 12.25c0 2.22 1.8 4.05 4 4.05z"/><path d="M12.56 6.6A11 11 0 0 0 14 3.02c.5 2.5 2 4.9 4 6.5s3 3.5 3 5.5a6.98 6.98 0 0 1-11.91 4.97"/></svg>',
    'chart':      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="3" x2="3" y2="21"/><line x1="3" y1="21" x2="21" y2="21"/><rect x="7" y="13" width="3" height="6"/><rect x="12" y="9" width="3" height="10"/><rect x="17" y="5" width="3" height="14"/></svg>',
    'gitbranch':  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
    'activity':   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
    'cloud':      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg>',
    'file':       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    'list':       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
    'flame':      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>',
    'dollar':     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
    'gauge':      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>',
    'users':      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    'archiveX':   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="m9.5 17 5-5"/><path d="m9.5 12 5 5"/></svg>',
    'userCog':    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="7" r="4"/><path d="M3 21v-2a4 4 0 0 1 4-4h4"/><circle cx="18" cy="15" r="3"/><path d="m21.7 16.4-.9-.3"/><path d="m15.2 13.9-.9-.3"/><path d="m16.6 18.7.3-.9"/><path d="m19.1 12.2.3-.9"/><path d="m19.6 18.7-.4-1"/><path d="m16.8 12.3-.4-1"/><path d="m14.3 16.6 1-.4"/><path d="m20.7 13.8 1-.4"/></svg>',
    'key':        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>',
    'logout':     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>'
  };

  // ---- Sidebar definition (groups + items) ----
  // Each item has a key matching APPS, plus optional `href` override.
  function buildNav(active) {
    var urls = window.AC_SHELL_URLS || {};
    function url(key) { return urls[key] || DEFAULT_URLS[key] || '#'; }

    var groups = [
      { label: 'Home', items: [
        { key: 'dashboard', icon: 'dashboard', text: 'Dashboard Overview', href: url('dashboard') }
      ]},
      { label: 'Asset Review', items: [
        { key: 'me-review', icon: 'wrench',    text: 'Mechanical Reviewer',  href: url('me-review') },
        { key: 'el-review', icon: 'zap',       text: 'Electrical Reviewer',  href: url('el-review') },
        { key: 'bf-review', icon: 'droplets',  text: 'Backflow Reviewer',    href: url('bf-review') },
        { key: 'kpis',      icon: 'chart',     text: 'Reviewer KPIs',        href: url('kpis') },
        { key: 'map',       icon: 'map',       text: 'Asset Map',            href: url('map') }
      ]},
      { label: 'SDI / Planon', items: [
        { key: 'sdi',       icon: 'gitbranch', text: 'SDI Process',           href: url('sdi') },
        { key: 'planon',    icon: 'cloud',     text: 'Planon Cloud',          href: 'https://ubc-acc.planoncloud.com/' },
        { key: 'tpl',       icon: 'file',      text: 'UBC SDI Templates',     href: '#' }
      ]},
      { label: 'Operations', items: [
        { key: 'logs',      icon: 'list',      text: 'Logs & Pending',        href: url('logs') },
        { key: 'fls',       icon: 'flame',     text: 'FLS Devices',           href: url('fls') },
        { key: 'life',      icon: 'activity',  text: 'Life Cycle Assessment', href: url('life') },
        { key: 'disposed',  icon: 'archiveX',  text: 'Disposed Assets',       href: url('disposed') },
        { key: 'cost',      icon: 'gauge',     text: 'Performance Analysis',  href: url('cost') },
        { key: 'useract',   icon: 'users',     text: 'User Activity',         href: url('useract') },
        { key: 'dict',      icon: 'book',      text: 'Dictionary',            href: url('dict') }
      ]},
      { label: 'Administration', items: [
        { key: 'admin',     icon: 'userCog',   text: 'User Admin',            href: url('admin') },
        { key: 'pwd',       icon: 'key',       text: 'Change Password',       href: url('pwd') },
        { key: 'logout',    icon: 'logout',    text: 'Logout',                href: url('logout') }
      ]}
    ];

    var html = '';
    groups.forEach(function (g) {
      html += '<div class="acshell-group-label">' + g.label + '</div>';
      g.items.forEach(function (it) {
        var cls = 'acshell-link' + (it.key === active ? ' acshell-active' : '');
        html += '<a class="' + cls + '" href="' + it.href + '">' +
                ICON[it.icon] + '<span>' + it.text + '</span></a>';
      });
    });
    return html;
  }

  function buildBreadcrumb(active) {
    var app = APPS[active] || APPS.dashboard;
    var urls = window.AC_SHELL_URLS || {};
    function url(key) { return urls[key] || DEFAULT_URLS[key] || '#'; }
    var parts = app.breadcrumb.slice();
    var last = parts.pop();
    var html = '';
    parts.forEach(function (p) {
      var href = p === 'Home' ? url('dashboard') : '#';
      html += '<a href="' + href + '">' + p + '</a><span class="acshell-sep">/</span>';
    });
    html += '<span class="acshell-current">' + last + '</span>';
    return html;
  }

  function initialsFromMeta() {
    var meta = document.querySelector('meta[name="acshell-user"]');
    if (meta && meta.content) {
      var name = meta.content.trim();
      var parts = name.split(/\s+/);
      var i = (parts[0] || '?').charAt(0) + (parts[1] ? parts[1].charAt(0) : '');
      return i.toUpperCase();
    }
    return 'GA';
  }

  function build(active) {
    var sidebar = document.createElement('aside');
    sidebar.className = 'acshell-sidebar';
    sidebar.setAttribute('data-acshell', 'sidebar');
    sidebar.innerHTML =
      '<div class="acshell-brand">' +
        '<img class="acshell-logo" src="/static/logos/ubc-facilities_logo.jpg" alt="UBC Facilities">' +
        '<div class="acshell-brand-text">' +
          '<div class="acshell-brand-title">Asset Management</div>' +
          '<div class="acshell-brand-sub">UBC Building Operations</div>' +
        '</div>' +
        '<button class="acshell-close" type="button" aria-label="Close menu" data-acshell-close>' + ICON.x + '</button>' +
      '</div>' +
      '<nav class="acshell-nav">' + buildNav(active) + '</nav>' +
      '<div class="acshell-foot">' +
        '<span>v2.3.0 · ' + ((window.AC_SHELL_ENV || 'Acceptance')) + '</span>' +
        '<span class="acshell-online">Online</span>' +
      '</div>';

    var topbar = document.createElement('header');
    topbar.className = 'acshell-topbar';
    topbar.setAttribute('data-acshell', 'topbar');
    topbar.innerHTML =
      '<button class="acshell-hamburger" type="button" aria-label="Open menu" data-acshell-open>' + ICON.menu + '</button>' +
      '<div class="acshell-breadcrumb">' + buildBreadcrumb(active) + '</div>' +
      '<div class="acshell-topbar-right">' +
        '<div class="acshell-globalsearch">' +
          '<span class="acshell-globalsearch-icon">' + ICON.search + '</span>' +
          '<input type="search" placeholder="Search assets, QR codes, buildings…" aria-label="Global search">' +
        '</div>' +
        '<button class="acshell-icon-btn" type="button" aria-label="Notifications">' + ICON.bell + '<span class="acshell-dot-danger"></span></button>' +
        '<div class="acshell-avatar" title="' + (document.querySelector('meta[name=acshell-user]') ? document.querySelector('meta[name=acshell-user]').content : 'User') + '">' + initialsFromMeta() + '</div>' +
      '</div>';

    return { sidebar: sidebar, topbar: topbar };
  }

  function wire(sidebar, topbar) {
    var openBtn = topbar.querySelector('[data-acshell-open]');
    var closeBtn = sidebar.querySelector('[data-acshell-close]');
    var backdrop = null;
    function open() {
      sidebar.classList.add('acshell-open');
      backdrop = document.createElement('div');
      backdrop.className = 'acshell-backdrop';
      backdrop.addEventListener('click', close);
      document.body.appendChild(backdrop);
    }
    function close() {
      sidebar.classList.remove('acshell-open');
      if (backdrop) { backdrop.remove(); backdrop = null; }
    }
    if (openBtn) openBtn.addEventListener('click', open);
    if (closeBtn) closeBtn.addEventListener('click', close);
  }

  function init() {
    // Guard: only run once
    if (document.querySelector('[data-acshell="sidebar"]')) return;
    if (!document.body) return;

    // Detect embedded mode (Dashboard iframes sub-apps; suppress chrome)
    var url = String(window.location.search || '');
    var isEmbedded = url.indexOf('embedded=true') !== -1 ||
                     (document.body.classList.contains('embedded-mode')) ||
                     (window.parent && window.parent !== window);
    if (isEmbedded) {
      document.body.classList.add('acshell-embedded');
      return;
    }

    var meta = document.querySelector('meta[name="acshell-active"]');
    var active = (meta && meta.content) ? meta.content : 'dashboard';
    if (!APPS[active]) active = 'dashboard';

    var parts = build(active);
    // Insert at the very top of body so existing content keeps its DOM position
    document.body.insertBefore(parts.topbar, document.body.firstChild);
    document.body.insertBefore(parts.sidebar, document.body.firstChild);
    document.body.classList.add('acshell-host');
    wire(parts.sidebar, parts.topbar);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
