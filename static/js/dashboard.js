/** 统计与运营后台：总览、分布、AI 效能、申诉处理、系统配置 */
(function () {
  'use strict';
  var A = window.API;
  if (!A.Auth.requireReviewer()) return;
  A.renderHeader('admin');

  var isAdmin = A.Auth.isAdmin();

  (async function load() {
    var root = A.$('#root');
    var ov, ai, appeals = { items: [] }, cfg = { items: [] };
    try {
      ov = await A.api('/admin/stats/overview');
    } catch (e) {
      root.innerHTML = '<div class="card"><div class="empty">加载失败：' + A.escapeHtml(e.message) + '</div></div>';
      return;
    }
    try { ai = await A.api('/admin/stats/ai-metrics'); } catch (e) { ai = null; }
    if (isAdmin) {
      try { appeals = await A.api('/admin/appeals?status=PENDING'); } catch (e) { /* ignore */ }
      try { cfg = await A.api('/admin/config'); } catch (e) { /* ignore */ }
    }

    var html = '';

    // 总览
    html += '<div class="card"><h2 class="card-title">📊 运营总览 <span class="hint">AI 初审 + 人工复核双审链路</span></h2>' +
      '<div class="stat-grid">' +
      stat(ov.total_reports, '累计上报') +
      stat(ov.today_reports, '今日新增') +
      stat(ov.pending_human, '待人工审核') +
      stat(ov.reviewing, '人工审核中') +
      stat(ov.need_supplement, '待补充材料') +
      stat(ov.approved, '审核通过') +
      stat(ov.rejected, '审核不通过') +
      stat(ov.appeal_pending, '待处理申诉') +
      stat((ov.approval_rate * 100).toFixed(1) + '%', '违规认定率') +
      stat(ov.avg_review_hours + ' h', '平均处理时长') +
      '</div></div>';

    // 分布
    html += '<div class="split" style="grid-template-columns:1fr 1fr">' +
      '<div class="card"><h3 class="card-title">按行为类型分布</h3>' + bars(ov.by_behavior) + '</div>' +
      '<div class="card"><h3 class="card-title">按状态分布</h3>' + bars(ov.by_status) + '</div>' +
      '</div>';

    html += '<div class="card"><h3 class="card-title">近 14 天上报趋势</h3>' + bars(ov.by_day) + '</div>';

    // AI 效能
    if (ai) {
      html += '<div class="card"><h3 class="card-title">AI 与人工审核工作量对照</h3>' +
        '<div class="stat-grid">' +
        stat(ai.ai_total, 'AI 初审次数') +
        stat(ai.ai_auto_closed, 'AI 自动结案') +
        stat(ai.human_total, '人工审核次数') +
        stat(ai.human_second_review, '二次复核次数') +
        '</div>' +
        (ai.ai_conclusion_distribution && ai.ai_conclusion_distribution.length
          ? '<div style="margin-top:14px">' + bars(ai.ai_conclusion_distribution.map(function (x) {
              return { label: A.AI_LABEL[x.key] || x.key, count: x.count };
            })) + '</div>'
          : '') +
        '<div class="notice" style="margin-top:12px">保守档策略：AI 仅提供建议，所有“疑似违规”结论均需人工复核；仅高置信“正常”可自动放行（当前默认关闭）。</div>' +
        '</div>';
    }

    // 申诉
    if (isAdmin) {
      html += '<div class="card"><h3 class="card-title">待处理申诉 <span class="hint">' + appeals.items.length + ' 条</span></h3>';
      if (!appeals.items.length) {
        html += '<div class="empty">暂无待处理申诉</div>';
      } else {
        html += appeals.items.map(function (a) {
          return '<div style="padding:10px 0;border-bottom:1px dashed var(--border)">' +
            '<div><b>上报 #' + a.report_id + '</b> <span class="subtle">' + A.fmtTime(a.created_at) + '</span></div>' +
            '<div class="subtle" style="margin:4px 0">申诉理由：' + A.escapeHtml(a.reason) + '</div>' +
            '<div class="btn-row">' +
            '<button class="btn btn-sm btn-success" data-appeal="' + a.id + '" data-act="ACCEPTED">申诉成立，改判</button>' +
            '<button class="btn btn-sm" data-appeal="' + a.id + '" data-act="REJECTED">申诉不成立，维持原判</button>' +
            '</div></div>';
        }).join('');
      }
      html += '</div>';

      // 配置
      html += '<div class="card"><h3 class="card-title">系统配置 <span class="hint">修改后立即生效</span></h3>' +
        cfg.items.map(function (c) {
          return '<div style="display:flex;gap:10px;align-items:center;padding:7px 0;border-bottom:1px dashed var(--border)">' +
            '<div style="flex:0 0 250px"><div class="mono" style="font-size:12.5px">' + A.escapeHtml(c.key) + '</div>' +
            '<div class="subtle" style="font-size:12px">' + A.escapeHtml(c.description || '') + '</div></div>' +
            '<input type="text" value="' + A.escapeHtml(c.value) + '" data-cfg="' + A.escapeHtml(c.key) + '">' +
            '<button class="btn btn-sm" data-save="' + A.escapeHtml(c.key) + '">保存</button>' +
            '</div>';
        }).join('') + '</div>';
    }

    root.innerHTML = html;
    bind();
  })();

  function stat(n, t) { return '<div class="stat"><div class="n">' + n + '</div><div class="t">' + t + '</div></div>'; }

  function bars(items) {
    if (!items || !items.length) return '<div class="empty">暂无数据</div>';
    var max = Math.max.apply(null, items.map(function (x) { return x.count; })) || 1;
    return items.map(function (x) {
      var w = Math.max(2, Math.round(x.count / max * 100));
      return '<div class="bar-row"><div class="k" title="' + A.escapeHtml(x.label) + '">' + A.escapeHtml(x.label) + '</div>' +
        '<div class="bar"><i style="width:' + w + '%"></i></div><div class="v">' + x.count + '</div></div>';
    }).join('');
  }

  function bind() {
    A.$$('[data-appeal]').forEach(function (btn) {
      btn.onclick = async function () {
        var accepted = btn.dataset.act === 'ACCEPTED';
        var comment = prompt(accepted ? '请填写改判说明：' : '请填写维持原判的复核说明：');
        if (comment === null) return;
        try {
          await A.api('/admin/appeals/' + btn.dataset.appeal + '/handle', {
            method: 'POST', body: { status: btn.dataset.act, comment: comment }
          });
          A.Toast.success('申诉已处理');
          setTimeout(function () { location.reload(); }, 700);
        } catch (e) { A.Toast.error(e.message); }
      };
    });

    A.$$('[data-save]').forEach(function (btn) {
      btn.onclick = async function () {
        var key = btn.dataset.save;
        var input = document.querySelector('[data-cfg="' + key + '"]');
        try {
          await A.api('/admin/config/' + encodeURIComponent(key) + '?value=' + encodeURIComponent(input.value), { method: 'PUT' });
          A.Toast.success('配置已更新：' + key);
        } catch (e) { A.Toast.error(e.message); }
      };
    });
  }
})();
