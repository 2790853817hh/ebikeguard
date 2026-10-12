/**
 * 共享前端基础库：请求封装、登录态、UI 小工具。
 * 无构建链，原生 ES5+/ES2017，直接 <script src> 引入。
 *
 * 【路径约定 · 强制】本文件内所有页面跳转与静态资源引用一律使用**绝对路径**（以 / 开头），
 * 例如 '/my-reports.html'、'/css/base.css'，禁止裸相对路径（如 'my-reports.html'）。
 *
 * 原因（2026-10-12 缺陷根因）：本文件被根级页面与 admin/ 子目录页面共用，
 * 裸相对路径会随当前页面所在目录解析。在 /admin/dashboard.html 下点击
 * href="review-workbench.html" 会跳到 /admin/review-workbench.html（不存在），
 * Nginx 的 try_files 兜底返回 index.html 的 HTML；该 HTML 里 <link href="css/base.css">
 * 又按 /admin/ 解析成 /admin/css/base.css 并再次兜底返回 HTML ——
 * 浏览器把 HTML 当 CSS 解析，整页样式丢失（表现为纯白裸排页面）。
 */
(function (global) {
  'use strict';

  var API_BASE = '/api/v1';
  var KEY_ACCESS = 'ev_access';
  var KEY_REFRESH = 'ev_refresh';
  var KEY_USER = 'ev_user';

  /**
   * 页面路径规范化：一律返回**站点根下的绝对路径**。
   * 约定：本应用固定部署在站点根（Nginx `root /opt/ev_audit/static`），
   * 因此 '/my-reports.html' 这类以 / 开头的路径在任何页面、任何 URL 形态下都稳定可解析。
   */
  function page(p) { return '/' + String(p).replace(/^\.?\//, ''); }

  var Auth = {
    get token() { return localStorage.getItem(KEY_ACCESS); },
    get refreshToken() { return localStorage.getItem(KEY_REFRESH); },
    get user() {
      try { return JSON.parse(localStorage.getItem(KEY_USER) || 'null'); } catch (e) { return null; }
    },
    get role() { return (this.user && this.user.role) || 'reporter'; },
    isReviewer: function () { return this.role === 'reviewer' || this.role === 'admin'; },
    isAdmin: function () { return this.role === 'admin'; },
    save: function (data) {
      localStorage.setItem(KEY_ACCESS, data.access_token);
      localStorage.setItem(KEY_REFRESH, data.refresh_token);
      localStorage.setItem(KEY_USER, JSON.stringify(data.user));
    },
    patchUser: function (u) { localStorage.setItem(KEY_USER, JSON.stringify(u)); },
    clear: function () {
      localStorage.removeItem(KEY_ACCESS);
      localStorage.removeItem(KEY_REFRESH);
      localStorage.removeItem(KEY_USER);
    },
    home: function () { return this.isReviewer() ? '/review-workbench.html' : '/my-reports.html'; },
    requireLogin: function () {
      if (!this.token) { location.replace('/index.html'); return false; }
      return true;
    },
    requireReviewer: function () {
      if (!this.requireLogin()) return false;
      if (!this.isReviewer()) { location.replace('/my-reports.html'); return false; }
      return true;
    },
    logout: function (silent) {
      var self = this;
      if (!silent && this.token) {
        api('/auth/logout', { method: 'POST' }).catch(function () {}).then(function () {
          self.clear(); location.replace('/index.html');
        });
      } else { this.clear(); location.replace('/index.html'); }
    }
  };

  function buildError(res, data) {
    var msg = (data && (data.message || data.detail && data.detail.message)) || ('请求失败（' + res.status + '）');
    var err = new Error(msg);
    err.status = res.status;
    err.code = data && data.code;
    err.payload = data;
    return err;
  }

  /** 统一请求封装 */
  async function api(path, options) {
    options = options || {};
    var url = path.indexOf('http') === 0 ? path : API_BASE + path;
    var headers = {};
    var init = { method: options.method || 'GET', headers: headers };

    if (options.auth !== false && Auth.token) headers['Authorization'] = 'Bearer ' + Auth.token;

    if (options.form) {
      init.body = options.form; // FormData，浏览器自动设置 Content-Type
    } else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }

    var res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      throw new Error('网络请求失败，请检查网络连接');
    }

    var data = null;
    var text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = { message: text }; } }

    if (!res.ok) {
      if (res.status === 401 && options.auth !== false) {
        Auth.clear();
        if (!/\/index\.html$/.test(location.pathname) && location.pathname !== '/') {
          Toast.error('登录已过期，请重新登录');
          setTimeout(function () { location.replace('/index.html'); }, 900);
        }
      }
      throw buildError(res, data);
    }
    return data;
  }

  /* ---------------- UI 工具 ---------------- */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function toastWrap() {
    var w = $('.toast-wrap');
    if (!w) { w = document.createElement('div'); w.className = 'toast-wrap'; document.body.appendChild(w); }
    return w;
  }

  var Toast = {
    show: function (msg, type, ms) {
      var el = document.createElement('div');
      el.className = 'toast' + (type ? ' ' + type : '');
      el.textContent = msg;
      toastWrap().appendChild(el);
      setTimeout(function () { el.style.opacity = '0'; el.style.transition = 'opacity .2s'; }, (ms || 2600) - 200);
      setTimeout(function () { el.remove(); }, ms || 2600);
    },
    error: function (m) { this.show(m, 'error', 3200); },
    success: function (m) { this.show(m, 'success'); }
  };

  var STATUS_TAG = {
    SUBMITTED: 'tag-blue', PENDING_AI: 'tag-blue', AI_DONE_PENDING_HUMAN: 'tag-orange',
    HUMAN_REVIEWING: 'tag-orange', NEED_SUPPLEMENT: 'tag-purple', APPROVED: 'tag-green',
    REJECTED: 'tag-gray', ESCALATED: 'tag-purple', APPEALING: 'tag-purple',
    ARCHIVED: 'tag-gray', WITHDRAWN: 'tag-gray', CANCELLED: 'tag-gray'
  };
  var STATUS_LABEL = {
    SUBMITTED: '已提交', PENDING_AI: 'AI 审核中', AI_DONE_PENDING_HUMAN: '待人工审核',
    HUMAN_REVIEWING: '人工审核中', NEED_SUPPLEMENT: '待补充材料', APPROVED: '审核通过',
    REJECTED: '审核不通过', ESCALATED: '疑难复核中', APPEALING: '申诉处理中',
    ARCHIVED: '已归档', WITHDRAWN: '已撤回', CANCELLED: '已作废'
  };
  var AI_LABEL = { VIOLATION: '疑似违规', NORMAL: '疑似正常', UNCERTAIN: '无法判定' };
  var AI_TAG = { VIOLATION: 'tag-red', NORMAL: 'tag-green', UNCERTAIN: 'tag-gray' };

  function statusTag(status) {
    return '<span class="tag ' + (STATUS_TAG[status] || 'tag-gray') + '">' + escapeHtml(STATUS_LABEL[status] || status) + '</span>';
  }
  function aiTag(conclusion, confidence) {
    if (!conclusion) return '<span class="tag tag-gray">未生成</span>';
    var pct = confidence === null || confidence === undefined ? '' : ' ' + Math.round(confidence * 100) + '%';
    return '<span class="tag ' + (AI_TAG[conclusion] || 'tag-gray') + '">' + escapeHtml(AI_LABEL[conclusion] || conclusion) + pct + '</span>';
  }

  function fmtTime(s) {
    if (!s) return '-';
    var d = new Date(s);
    if (isNaN(d.getTime())) return s;
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  function query(name) {
    return new URLSearchParams(location.search).get(name);
  }

  function renderHeader(active) {
    var user = Auth.user;
    var links = '';
    if (user) {
      if (Auth.isReviewer()) {
        links = '<a href="/review-workbench.html" class="' + (active === 'review' ? 'active' : '') + '">审核工作台</a>' +
                '<a href="/my-reports.html" class="' + (active === 'mine' ? 'active' : '') + '">全部上报</a>' +
                (Auth.isAdmin() ? '<a href="/admin/dashboard.html" class="' + (active === 'admin' ? 'active' : '') + '">统计后台</a>' : '');
      } else {
        links = '<a href="/upload.html" class="' + (active === 'upload' ? 'active' : '') + '">我要上报</a>' +
                '<a href="/my-reports.html" class="' + (active === 'mine' ? 'active' : '') + '">我的上报</a>';
      }
      links += '<a href="/notifications.html" class="' + (active === 'notify' ? 'active' : '') + '">通知' +
               '<span class="badge" id="navBadge" style="display:none"></span></a>';
    }
    var who = user ? ((user.real_name || user.username || (user.phone || '').replace(/(\d{3})\d{4}(\d{4})/, '$1****$2')) +
      ' · ' + ({ reporter: '上报人', reviewer: '审核员', admin: '管理员' }[user.role] || user.role)) : '';
    var html =
      '<header class="app-header">' +
        '<div class="logo">电动车安全上报</div>' +
        '<nav class="main-nav">' + links + '</nav>' +
        '<div class="spacer"></div>' +
        '<div class="who">' + escapeHtml(who) + '</div>' +
        (user ? '<button class="btn btn-sm" id="btnLogout">退出</button>' : '') +
      '</header>';
    document.body.insertAdjacentHTML('afterbegin', html);
    var btn = $('#btnLogout');
    if (btn) btn.onclick = function () { Auth.logout(); };
    if (user) {
      refreshUnreadBadge();
      startBadgeLoop();
    }
  }

  var _badgeTimer = null;

  function startBadgeLoop() {
    if (_badgeTimer) return;
    _badgeTimer = setInterval(function () {
      if (document.visibilityState === 'visible') refreshUnreadBadge();
    }, 60000);
  }

  async function refreshUnreadBadge() {
    var badge = $('#navBadge');
    if (!badge) return;
    try {
      var data = await api('/notifications/unread-count');
      var n = data.unread || 0;
      badge.textContent = n > 99 ? '99+' : String(n);
      badge.style.display = n > 0 ? '' : 'none';
    } catch (e) { /* 未登录或网络异常时忽略 */ }
  }

  function notice(html, type) {
    return '<div class="notice ' + (type || '') + '">' + html + '</div>';
  }

  global.API = {
    Auth: Auth,
    api: api,
    page: page,
    $: $, $$: $$,
    escapeHtml: escapeHtml,
    Toast: Toast,
    statusTag: statusTag,
    aiTag: aiTag,
    fmtTime: fmtTime,
    uuid: uuid,
    query: query,
    renderHeader: renderHeader,
    refreshUnreadBadge: refreshUnreadBadge,
    notice: notice,
    STATUS_LABEL: STATUS_LABEL,
    AI_LABEL: AI_LABEL
  };
})(window);
