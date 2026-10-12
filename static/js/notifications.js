/** 通知中心：站内信列表（分页/仅未读）+ 接收邮箱设置 */
(function () {
  'use strict';
  var A = window.API;
  if (!A.Auth.requireLogin()) return;
  A.renderHeader('notify');

  var page = 1, pageSize = 10, total = 0, unreadOnly = false;

  async function load(append) {
    var box = A.$('#listBox');
    if (!append) box.innerHTML = '<div class="empty">加载中…</div>';
    var url = '/notifications?page=' + page + '&page_size=' + pageSize + (unreadOnly ? '&unread_only=true' : '');
    try {
      var data = await A.api(url);
      total = data.total;
      A.$('#totalHint').textContent = '共 ' + total + ' 条';
      var html = data.items.map(itemHtml).join('');
      if (!data.items.length && !append) {
        html = '<div class="empty"><div class="big">🔔</div>暂无通知</div>';
      }
      if (append) box.insertAdjacentHTML('beforeend', html);
      else box.innerHTML = html;
      A.$$('#listBox .notify-item').forEach(function (el) {
        el.onclick = function () { openItem(el); };
      });
      A.$('#btnMore').style.display = page * pageSize < total ? '' : 'none';
    } catch (e) {
      box.innerHTML = '<div class="empty">加载失败：' + A.escapeHtml(e.message) + '</div>';
    }
  }

  function itemHtml(it) {
    return '<div class="report-item notify-item" data-id="' + it.id + '" data-report="' + (it.report_id || '') +
        '" data-read="' + (it.is_read ? '1' : '0') + '">' +
      '<div class="body">' +
        '<div class="title ellipsis">' + (it.is_read ? '' : '<span class="badge-dot"></span>') +
          A.escapeHtml(it.title) + '</div>' +
        '<div class="meta"><span class="ellipsis" style="max-width:100%">' + A.escapeHtml(it.content) + '</span></div>' +
        '<div class="meta"><span>' + A.escapeHtml(it.created_at_cn || A.fmtTime(it.created_at)) + '</span></div>' +
      '</div>' +
      '<div style="text-align:right;color:var(--muted)">›</div>' +
    '</div>';
  }

  async function openItem(el) {
    if (el.dataset.read !== '1') {
      try {
        await A.api('/notifications/' + el.dataset.id + '/read', { method: 'POST' });
        el.dataset.read = '1';
        var dot = el.querySelector('.badge-dot');
        if (dot) dot.parentNode.removeChild(dot);
        A.refreshUnreadBadge();
      } catch (e) { /* 标记失败不阻塞跳转 */ }
    }
    var rid = el.dataset.report;
    if (rid) location.href = '/report-detail.html?id=' + rid;
  }

  async function loadEmail() {
    try {
      var me = await A.api('/users/me');
      if (me.email) A.$('#emailInput').value = me.email;
    } catch (e) { /* 忽略 */ }
  }

  A.$('#btnSaveEmail').onclick = async function () {
    var val = A.$('#emailInput').value.trim();
    try {
      var me = await A.api('/users/me/email', { method: 'PUT', body: { email: val } });
      A.$('#emailStatus').textContent = me.email ? ('已保存：' + me.email) : '已清除邮箱（不再发送邮件通知）';
      A.Toast.success('已保存');
    } catch (e) {
      A.$('#emailStatus').textContent = '';
      A.Toast.error(e.message);
    }
  };

  A.$('#btnUnread').onclick = function () {
    unreadOnly = !unreadOnly;
    this.classList.toggle('btn-primary', unreadOnly);
    this.textContent = unreadOnly ? '显示全部' : '只看未读';
    page = 1;
    load(false);
  };
  A.$('#btnRefresh').onclick = function () { page = 1; load(false); loadEmail(); };
  A.$('#btnMore').onclick = function () { page++; load(true); };

  load(false);
  loadEmail();
})();
