/** 上报列表页（上报人看自己的；审核员/管理员看全部） */
(function () {
  'use strict';
  var A = window.API;
  if (!A.Auth.requireLogin()) return;
  A.renderHeader('mine');

  var page = 1, pageSize = 10, total = 0;
  var isReviewer = A.Auth.isReviewer();
  if (isReviewer) A.$('#pageTitle').textContent = '全部上报';

  async function load(append) {
    var status = A.$('#filterStatus').value;
    var box = A.$('#listBox');
    if (!append) box.innerHTML = '<div class="empty">加载中…</div>';

    var url = '/reports?page=' + page + '&page_size=' + pageSize + (status ? '&status=' + encodeURIComponent(status) : '');
    try {
      var data = await A.api(url);
      total = data.total;
      A.$('#totalHint').textContent = '共 ' + total + ' 条';
      var html = data.items.map(itemHtml).join('');
      if (!data.items.length && !append) {
        html = '<div class="empty"><div class="big">🗂️</div>还没有上报记录<br><a href="upload.html">立即上报一条</a></div>';
      }
      if (append) box.insertAdjacentHTML('beforeend', html);
      else box.innerHTML = html;
      A.$$('#listBox .report-item').forEach(function (el) {
        el.onclick = function () { location.href = 'report-detail.html?id=' + el.dataset.id; };
      });
      A.$('#btnMore').style.display = page * pageSize < total ? '' : 'none';
    } catch (e) {
      box.innerHTML = '<div class="empty">加载失败：' + A.escapeHtml(e.message) + '</div>';
    }
  }

  function itemHtml(it) {
    var thumb = it.cover_url
      ? '<img class="thumb" src="' + A.escapeHtml(it.cover_url) + '" alt="" loading="lazy">'
      : '<div class="thumb" style="display:flex;align-items:center;justify-content:center;color:#9aa4b2">无图</div>';
    return '<div class="report-item" data-id="' + it.id + '">' + thumb +
      '<div class="body">' +
        '<div class="title ellipsis">' + A.escapeHtml(it.behavior_type_name || '未知类型') + ' ' + A.statusTag(it.status) + '</div>' +
        '<div class="meta"><span class="ellipsis" style="max-width:100%">📍 ' + A.escapeHtml(it.address_text) + '</span></div>' +
        '<div class="meta"><span class="mono">' + A.escapeHtml(it.report_no) + '</span><span>' + A.fmtTime(it.submitted_at) + '</span></div>' +
      '</div>' +
      '<div style="text-align:right">' + A.aiTag(it.ai_conclusion, it.ai_confidence) + '</div>' +
    '</div>';
  }

  A.$('#filterStatus').onchange = function () { page = 1; load(false); };
  A.$('#btnRefresh').onclick = function () { page = 1; load(false); };
  A.$('#btnMore').onclick = function () { page++; load(true); };
  load(false);
})();
