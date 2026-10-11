/** 上报详情页：信息、图片、AI 建议、处理进度、上报人可执行动作 */
(function () {
  'use strict';
  var A = window.API;
  if (!A.Auth.requireLogin()) return;
  A.renderHeader('mine');

  var id = A.query('id');
  if (!id) { A.$('#root').innerHTML = '<div class="card"><div class="empty">缺少上报编号</div></div>'; return; }

  var current = null;

  async function load() {
    try {
      current = await A.api('/reports/' + id);
      render();
    } catch (e) {
      A.$('#root').innerHTML = '<div class="card"><div class="empty">加载失败：' + A.escapeHtml(e.message) + '</div></div>';
    }
  }

  function render() {
    var d = current;
    var html = '';

    html += '<div class="card">' +
      '<h2 class="card-title">' + A.escapeHtml(d.behavior_type_name || '未知类型') + ' ' + A.statusTag(d.status) + '</h2>' +
      '<div class="subtle mono" style="margin-bottom:10px">受理编号 ' + A.escapeHtml(d.report_no) + ' · 提交于 ' + A.fmtTime(d.submitted_at) + '</div>' +
      '<div class="thumb-grid">' +
        d.images.map(function (im) {
          return '<a class="cell" href="' + A.escapeHtml(im.url) + '" target="_blank" rel="noopener">' +
                 '<img src="' + A.escapeHtml(im.thumb_url) + '" alt="" loading="lazy"></a>';
        }).join('') +
      '</div>' +
    '</div>';

    // 结论 / 待办提示
    if (d.status === 'APPROVED') {
      html += '<div class="card"><div class="notice">' + A.escapeHtml(d.final_comment || '经核实，您上报的情况确实存在安全隐患，已转交相关部门处理。') + '</div></div>';
    } else if (d.status === 'REJECTED') {
      html += '<div class="card"><div class="notice warn">本次未能受理' +
        (d.reject_reason_label ? '，原因：' + A.escapeHtml(d.reject_reason_label) : '') +
        (d.final_comment ? '。审核说明：' + A.escapeHtml(d.final_comment) : '') +
        '。如仍有疑问可提交补充材料或发起申诉。</div></div>';
    } else if (d.status === 'NEED_SUPPLEMENT') {
      html += '<div class="card"><div class="notice danger">请补充材料' +
        (d.supplement_deadline ? '，截止时间 ' + A.fmtTime(d.supplement_deadline) : '') +
        '。' + A.escapeHtml(d.final_comment || '') + '</div></div>';
    } else if (d.status === 'APPEALING') {
      html += '<div class="card"><div class="notice">申诉已受理，我们正在复核，结果会通过站内消息通知您。</div></div>';
    }

    // 详细信息
    html += '<div class="card"><h3 class="card-title">上报信息</h3>' +
      row('发生地址', A.escapeHtml(d.address_text)) +
      row('行为类型', A.escapeHtml(d.behavior_type_name || '-')) +
      row('定位来源', { geolocation: '浏览器定位', exif: '照片 EXIF', manual: '手动填写', map: '地图选点', unknown: '未知' }[d.location_source] || '-') +
      row('经纬度', d.longitude !== null && d.longitude !== undefined ? (d.longitude + ', ' + d.latitude) : '未获取') +
      row('定位一致性', d.location_conflict
        ? '<span class="tag tag-orange">定位与照片位置不一致，已提示审核员核实</span>'
        : '<span class="tag tag-green">一致</span>') +
      row('拍摄时间', d.shoot_time ? A.escapeHtml(d.shoot_time.replace('T', ' ')) : '-') +
      row('补充说明', A.escapeHtml(d.remark || '-')) +
      row('照片张数', d.images.length + ' 张') +
      '</div>';

    // AI 初审
    if (d.ai_result) {
      html += '<div class="card"><h3 class="card-title">AI 自动初审 <span class="hint">仅作辅助建议，最终以人工复核为准</span></h3>' +
        '<div style="margin-bottom:10px">' + A.aiTag(d.ai_result.conclusion, d.ai_result.confidence) +
        ' <span class="subtle">模型 ' + A.escapeHtml(d.ai_result.model_name) + ' · ' + A.fmtTime(d.ai_result.created_at) + '</span></div>' +
        '<ul style="margin:0;padding-left:18px">' +
        d.ai_result.reasons.map(function (r) {
          return '<li class="subtle">' + A.escapeHtml(r.text) + '</li>';
        }).join('') +
        '</ul></div>';
    }

    // 处理进度
    html += '<div class="card"><h3 class="card-title">处理进度</h3><ul class="timeline">' +
      d.timeline.map(function (t) {
        return '<li><div class="t-title">' + A.escapeHtml(t.label) + '</div>' +
               '<div class="t-meta">' + A.escapeHtml(t.actor || '系统') + ' · ' + A.fmtTime(t.at) + '</div>' +
               (t.comment ? '<div class="t-meta">说明：' + A.escapeHtml(t.comment) + '</div>' : '') +
               '</li>';
      }).join('') +
      '</ul></div>';

    // 动作
    var actions = '';
    if (d.can_supplement) actions += '<button class="btn btn-primary" id="btnSupplement">补充材料</button>';
    if (d.can_appeal) actions += '<button class="btn" id="btnAppeal">我要申诉</button>';
    if (d.can_withdraw) actions += '<button class="btn btn-danger" id="btnWithdraw">撤回上报</button>';
    if (actions) html += '<div class="card"><div class="btn-row">' + actions + '</div></div>';

    A.$('#root').innerHTML = html;
    bind();
  }

  function row(k, v) {
    return '<div style="display:flex;gap:12px;padding:6px 0;border-bottom:1px dashed var(--border)">' +
      '<div class="subtle" style="flex:0 0 88px">' + k + '</div>' +
      '<div style="flex:1">' + v + '</div></div>';
  }

  function bind() {
    var w = A.$('#btnWithdraw');
    if (w) w.onclick = async function () {
      if (!confirm('确认撤回该上报？撤回后不可恢复。')) return;
      try {
        await A.api('/reports/' + id + '/withdraw', { method: 'POST' });
        A.Toast.success('已撤回'); load();
      } catch (e) { A.Toast.error(e.message); }
    };

    var s = A.$('#btnSupplement');
    if (s) s.onclick = async function () {
      var remark = prompt('请说明补充内容（例如：已补充白天拍摄的门牌号照片）：');
      if (remark === null) return;
      var fd = new FormData();
      fd.append('remark', remark || '');
      fd.append('client_uuid', A.uuid());
      var res = await A.api('/reports/' + id + '/supplement', { method: 'POST', form: fd });
      A.Toast.success('补充已提交，将重新进入审核流程');
      setTimeout(load, 600);
      return res;
    };

    var ap = A.$('#btnAppeal');
    if (ap) ap.onclick = async function () {
      var reason = prompt('请填写申诉理由（不少于 5 个字）：');
      if (reason === null) return;
      if (reason.trim().length < 5) { A.Toast.error('申诉理由过短'); return; }
      try {
        await A.api('/reports/' + id + '/appeal', { method: 'POST', body: { reason: reason.trim() } });
        A.Toast.success('申诉已提交'); load();
      } catch (e) { A.Toast.error(e.message); }
    };
  }

  load();
})();
