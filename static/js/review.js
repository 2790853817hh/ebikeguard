/** 审核工作台：队列 + 认领 + 结论提交（含 AI 建议、留痕） */
(function () {
  'use strict';
  var A = window.API;
  if (!A.Auth.requireReviewer()) return;
  A.renderHeader('review');

  var page = 1, pageSize = 15, total = 0;
  var currentId = null;
  var reasons = [];

  /* ---------------- 初始化 ---------------- */
  (async function init() {
    try {
      var list = await A.api('/behavior-types');
      A.$('#fBehavior').innerHTML = '<option value="">全部行为类型</option>' + list.map(function (b) {
        return '<option value="' + A.escapeHtml(b.code) + '">' + A.escapeHtml(b.name) + '</option>';
      }).join('');
    } catch (e) { /* 忽略 */ }
    try {
      reasons = await A.api('/reject-reasons');
    } catch (e) { reasons = []; }
    loadQueue(false);
  })();

  /* ---------------- 队列 ---------------- */
  async function loadQueue(append) {
    var box = A.$('#queueBox');
    if (!append) box.innerHTML = '<div class="empty">加载中…</div>';

    var qs = [
      'page=' + page,
      'page_size=' + pageSize,
      'status=' + encodeURIComponent(A.$('#fStatus').value || 'AI_DONE_PENDING_HUMAN'),
      'order=' + encodeURIComponent(A.$('#fOrder').value)
    ];
    var kw = A.$('#fKeyword').value.trim();
    if (kw) qs.push('keyword=' + encodeURIComponent(kw));
    if (A.$('#fAi').value) qs.push('ai_conclusion=' + encodeURIComponent(A.$('#fAi').value));

    try {
      var data = await A.api('/review/queue?' + qs.join('&'));
      total = data.total;
      A.$('#queueHint').textContent = '共 ' + total + ' 条';
      var html = data.items.map(itemHtml).join('');
      if (!data.items.length && !append) html = '<div class="empty"><div class="big">✅</div>暂无待处理任务</div>';
      if (append) box.insertAdjacentHTML('beforeend', html);
      else box.innerHTML = html;
      A.$$('#queueBox .report-item').forEach(function (el) {
        el.onclick = function () {
          A.$$('#queueBox .report-item').forEach(function (x) { x.style.background = ''; });
          el.style.background = 'var(--primary-soft)';
          openDetail(el.dataset.id);
        };
      });
      A.$('#btnMore').style.display = page * pageSize < total ? '' : 'none';
    } catch (e) {
      box.innerHTML = '<div class="empty">加载失败：' + A.escapeHtml(e.message) + '</div>';
    }
  }

  function itemHtml(it) {
    var overdue = it.waiting_hours > 24 ? ' <span class="tag tag-red">超 ' + Math.round(it.waiting_hours) + 'h</span>' : '';
    var thumb = it.cover_url
      ? '<img class="thumb" src="' + A.escapeHtml(it.cover_url) + '" alt="" loading="lazy">'
      : '<div class="thumb" style="display:flex;align-items:center;justify-content:center;color:#9aa4b2">无图</div>';
    return '<div class="report-item" data-id="' + it.id + '">' + thumb +
      '<div class="body">' +
        '<div class="title ellipsis">' + A.escapeHtml(it.behavior_type_name || '-') + ' ' + A.statusTag(it.status) + overdue + '</div>' +
        '<div class="meta"><span class="ellipsis" style="max-width:100%">📍 ' + A.escapeHtml(it.address_text) + '</span></div>' +
        '<div class="meta"><span class="mono">' + A.escapeHtml(it.report_no) + '</span><span>' + A.fmtTime(it.submitted_at) + '</span>' +
        (it.assigned_reviewer_name ? '<span>认领：' + A.escapeHtml(it.assigned_reviewer_name) + '</span>' : '') + '</div>' +
      '</div></div>';
  }

  /* ---------------- 详情与裁决 ---------------- */
  async function openDetail(id) {
    currentId = id;
    var box = A.$('#detailBox');
    box.innerHTML = '<div class="empty">加载中…</div>';
    var d;
    try { d = await A.api('/review/' + id); }
    catch (e) { box.innerHTML = '<div class="empty">加载失败：' + A.escapeHtml(e.message) + '</div>'; return; }

    var r = d.report;
    var html = '';

    html += '<h2 class="card-title">' + A.escapeHtml(r.behavior_type_name || '-') + ' ' + A.statusTag(r.status) + '</h2>' +
      '<div class="subtle mono" style="margin-bottom:10px">' + A.escapeHtml(r.report_no) + ' · 提交 ' + A.fmtTime(r.submitted_at) +
      ' · 已等待 ' + r.waiting_hours + ' 小时</div>';

    html += '<div class="thumb-grid" style="margin-bottom:14px">' + d.images.map(function (im) {
      return '<a class="cell" href="' + A.escapeHtml(im.url) + '" target="_blank" rel="noopener"><img src="' + A.escapeHtml(im.thumb_url) + '" alt="" loading="lazy"></a>';
    }).join('') + '</div>';

    // AI 建议
    if (d.ai_result) {
      html += '<div class="card" style="box-shadow:none;background:#fafbfc">' +
        '<div style="margin-bottom:8px"><b>AI 初审建议</b> ' + A.aiTag(d.ai_result.conclusion, d.ai_result.confidence) +
        '<span class="subtle" style="margin-left:6px">' + A.escapeHtml(d.ai_result.model_name) + '</span></div>' +
        '<ul style="margin:0;padding-left:18px">' + d.ai_result.reasons.map(function (x) {
          return '<li class="subtle">' + A.escapeHtml(x.text) + '</li>';
        }).join('') + '</ul>' +
        '<div class="notice warn" style="margin-top:8px">AI 结论仅作参考，请以现场照片与实际判断为准。</div>' +
        '</div>';
    }

    // 上报信息
    html += '<div class="card" style="box-shadow:none">' +
      kv('行为类型', A.escapeHtml(r.behavior_type_name || '-')) +
      kv('发生地址', A.escapeHtml(r.address_text)) +
      kv('经纬度', r.longitude !== null && r.longitude !== undefined ? (r.longitude + ', ' + r.latitude) : '未获取') +
      kv('定位来源', A.escapeHtml(r.location_source || '-')) +
      kv('定位冲突', r.location_conflict ? '<span class="tag tag-orange">是（照片位置与上报位置不一致）</span>' : '否') +
      kv('拍摄时间', A.escapeHtml((r.shoot_time || '-').replace('T', ' '))) +
      kv('备注', A.escapeHtml(r.remark || '-')) +
      kv('上报人', A.escapeHtml(r.reporter_masked || '-')) +
      '</div>';

    // 历史
    if (d.history && d.history.length) {
      html += '<div class="card" style="box-shadow:none"><b>审核历史</b><ul class="timeline" style="margin-top:8px">' +
        d.history.map(function (h) {
          return '<li><div class="t-title">' + A.escapeHtml(decisionLabel(h.decision)) + (h.is_second_review ? '（二次复核）' : '') + '</div>' +
            '<div class="t-meta">' + A.escapeHtml(h.reviewer) + ' · ' + A.fmtTime(h.created_at) + '</div>' +
            (h.reason_label ? '<div class="t-meta">原因：' + A.escapeHtml(h.reason_label) + '</div>' : '') +
            (h.comment ? '<div class="t-meta">说明：' + A.escapeHtml(h.comment) + '</div>' : '') + '</li>';
        }).join('') + '</ul></div>';
    }

    // 操作区
    html += '<div class="actions-sticky">';
    if (r.status === 'ESCALATED' && !d.claimed_by_me) {
      html += '<div class="notice warn" style="margin-bottom:10px">该件已升级疑难复核，需由非原审核员或管理员裁决。</div>';
    }
    html += '<label class="field"><span class="lbl">审核意见（驳回/退回必填）</span>' +
      '<textarea id="reviewComment" placeholder="请填写核实情况或需要上报人补充的内容"></textarea></label>' +
      '<label class="field" id="reasonField" style="display:none"><span class="lbl">驳回原因 <span class="req">*</span></span>' +
      '<select id="reviewReason">' + reasons.map(function (x) {
        return '<option value="' + A.escapeHtml(x.code) + '">' + A.escapeHtml(x.label) + '</option>';
      }).join('') + '</select></label>';

    if (r.status === 'AI_DONE_PENDING_HUMAN') {
      html += '<div class="btn-row"><button class="btn btn-primary btn-block" id="btnClaim">认领该任务</button></div>';
    } else if (r.status === 'HUMAN_REVIEWING' || r.status === 'ESCALATED') {
      html += '<div class="btn-row">' +
        '<button class="btn btn-success" id="btnApprove" style="flex:1">✔ 通过（违规成立）</button>' +
        '<button class="btn btn-danger" id="btnReject" style="flex:1">✘ 不通过</button>' +
        '</div><div class="btn-row" style="margin-top:8px">' +
        (r.status === 'ESCALATED' ? '' : '<button class="btn" id="btnSupplement" style="flex:1">退回补充材料</button>') +
        (r.status === 'ESCALATED' ? '' : '<button class="btn" id="btnEscalate" style="flex:1">标记疑难升级</button>') +
        '</div>';
    } else {
      html += '<div class="notice">该记录当前状态为「' + A.escapeHtml(r.status_label) + '」，无需审核操作。</div>';
    }
    html += '</div>';

    box.innerHTML = html;
    bindActions(r);
  }

  function kv(k, v) {
    return '<div style="display:flex;gap:12px;padding:6px 0;border-bottom:1px dashed var(--border)">' +
      '<div class="subtle" style="flex:0 0 80px">' + k + '</div><div style="flex:1">' + v + '</div></div>';
  }

  function decisionLabel(d) {
    return { APPROVED: '审核通过', REJECTED: '审核不通过', NEED_SUPPLEMENT: '退回补充材料', ESCALATED: '转疑难复核' }[d] || d;
  }

  function bindActions(r) {
    var c = A.$('#btnClaim');
    if (c) c.onclick = async function () {
      try {
        await A.api('/review/' + currentId + '/claim', { method: 'POST' });
        A.Toast.success('认领成功'); refreshBoth();
      } catch (e) { A.Toast.error(e.message); }
    };

    var ap = A.$('#btnApprove');
    if (ap) ap.onclick = function () { submitDecision('APPROVED'); };

    var rj = A.$('#btnReject');
    if (rj) {
      var rejectArmed = false;
      rj.onclick = function () {
        if (!rejectArmed) {
          rejectArmed = true;
          A.$('#reasonField').style.display = '';
          rj.textContent = '✘ 确认不通过';
          A.Toast.show('请选择驳回原因后再次点击「确认不通过」');
          var rf = A.$('#reasonField');
          if (rf && rf.scrollIntoView) rf.scrollIntoView({ behavior: 'smooth', block: 'center' });
          return;
        }
        submitDecision('REJECTED');
      };
    }

    var sp = A.$('#btnSupplement');
    if (sp) sp.onclick = function () { submitDecision('NEED_SUPPLEMENT'); };

    var es = A.$('#btnEscalate');
    if (es) es.onclick = function () {
      if (!confirm('确认将该记录标记为疑难升级（转二次复核）？')) return;
      submitDecision('ESCALATED');
    };
  }

  async function submitDecision(decision) {
    var body = {
      decision: decision,
      comment: (A.$('#reviewComment') || {}).value || ''
    };
    if (decision === 'REJECTED') body.reason_code = A.$('#reviewReason') ? A.$('#reviewReason').value : null;
    try {
      var res = await A.api('/review/' + currentId + '/decision', { method: 'POST', body: body });
      A.Toast.success('审核结论已提交：' + (A.STATUS_LABEL[res.status] || res.status));
      refreshBoth();
    } catch (e) { A.Toast.error(e.message); }
  }

  function refreshBoth() {
    loadQueue(false);
    if (currentId) openDetail(currentId);
  }

  /* ---------------- 事件 ---------------- */
  A.$('#btnSearch').onclick = function () { page = 1; loadQueue(false); };
  A.$('#btnMore').onclick = function () { page++; loadQueue(true); };
  A.$('#fStatus').onchange = function () { page = 1; loadQueue(false); };
  A.$('#fOrder').onchange = function () { page = 1; loadQueue(false); };
  A.$('#fKeyword').addEventListener('keydown', function (e) { if (e.key === 'Enter') { page = 1; loadQueue(false); } });
})();
