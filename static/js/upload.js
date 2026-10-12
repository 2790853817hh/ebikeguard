/** 拍照上报页逻辑 */
(function () {
  'use strict';
  var A = window.API;
  if (!A.Auth.requireLogin()) return;
  A.renderHeader('upload');

  var files = [];               // 已选择的 File 列表
  var geo = { lng: null, lat: null, source: 'unknown' };
  var maxImages = 9;

  /* ---------------- 初始化 ---------------- */
  (async function init() {
    try {
      var cfg = await A.api('/config/public');
      maxImages = cfg.max_images || 9;
      A.$('#maxImages').textContent = maxImages;
      A.$('#privacyNotice').innerHTML = '🔒 ' + A.escapeHtml(cfg.privacy_notice || '请勿拍摄可识别人脸、车牌的画面。');
    } catch (e) { /* 忽略 */ }

    try {
      var list = await A.api('/behavior-types');
      var sel = A.$('#behaviorType');
      sel.innerHTML = '<option value="">请选择行为类型</option>' + list.map(function (b) {
        return '<option value="' + A.escapeHtml(b.code) + '">' + A.escapeHtml(b.name) + '</option>';
      }).join('');
    } catch (e) {
      A.$('#behaviorType').innerHTML = '<option value="">加载失败，请刷新重试</option>';
    }

    var now = new Date();
    A.$('#shootTime').value = localDateTime(now);
  })();

  function localDateTime(d) {
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  /* ---------------- 图片选择 ---------------- */
  A.$('#photoBox').onclick = function () { A.$('#fileInput').click(); };

  A.$('#fileInput').onchange = function (ev) {
    var picked = Array.prototype.slice.call(ev.target.files || []);
    if (!picked.length) return;
    for (var i = 0; i < picked.length; i++) {
      if (files.length >= maxImages) { A.Toast.error('最多上传 ' + maxImages + ' 张照片'); break; }
      var f = picked[i];
      if (!/^image\//.test(f.type)) { A.Toast.error('「' + f.name + '」不是图片文件'); continue; }
      if (f.size > 15 * 1024 * 1024) { A.Toast.error('「' + f.name + '」超过 15MB'); continue; }
      files.push(f);
    }
    ev.target.value = '';
    renderThumbs();
  };

  function renderThumbs() {
    A.$('#thumbGrid').innerHTML = files.map(function (f, idx) {
      return '<div class="cell"><img src="' + URL.createObjectURL(f) + '" alt="">' +
             '<button class="del" data-idx="' + idx + '" type="button">✕</button></div>';
    }).join('');
    A.$$('#thumbGrid .del').forEach(function (btn) {
      btn.onclick = function () {
        files.splice(Number(btn.dataset.idx), 1);
        renderThumbs();
      };
    });
  }

  /* ---------------- 定位 ---------------- */
  A.$('#btnLocate').onclick = function () {
    var btn = this;
    if (!navigator.geolocation) { A.Toast.error('当前浏览器不支持定位'); return; }
    btn.disabled = true;
    A.$('#locStatus').textContent = '定位中…';
    navigator.geolocation.getCurrentPosition(function (pos) {
      geo.lng = Number(pos.coords.longitude.toFixed(6));
      geo.lat = Number(pos.coords.latitude.toFixed(6));
      geo.source = 'geolocation';
      A.$('#coordText').value = geo.lng + ', ' + geo.lat;
      A.$('#locStatus').textContent = '定位成功';
      btn.disabled = false;
      reverseGeo();
    }, function (err) {
      btn.disabled = false;
      A.$('#locStatus').textContent = '';
      A.Toast.error('定位失败：' + (err.message || '请检查浏览器定位权限（需 HTTPS 环境）') + '，可手动填写地址');
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  };

  async function reverseGeo() {
    try {
      var res = await A.api('/geo/reverse', { method: 'POST', body: { longitude: geo.lng, latitude: geo.lat } });
      if (res.address && !A.$('#addressText').value.trim()) {
        A.$('#addressText').value = res.address;
      }
    } catch (e) { /* 逆地理编码失败不阻塞上报 */ }
  }

  /* ---------------- 提交 ---------------- */
  A.$('#btnSubmit').onclick = async function () {
    var behavior = A.$('#behaviorType').value;
    var address = A.$('#addressText').value.trim();
    if (!files.length) { A.Toast.error('请至少拍摄或选择一张现场照片'); return; }
    if (!behavior) { A.Toast.error('请选择行为类型'); return; }
    if (!address) { A.Toast.error('请填写发生地址'); return; }

    var btn = this;
    btn.disabled = true; btn.textContent = '正在提交…';

    var fd = new FormData();
    files.forEach(function (f) { fd.append('images', f, f.name); });
    fd.append('behavior_type_code', behavior);
    fd.append('address_text', address);
    fd.append('remark', A.$('#remark').value.trim());
    var emailEl = A.$('#contactEmail');
    if (emailEl && emailEl.value.trim()) { fd.append('contact_email', emailEl.value.trim()); }
    fd.append('shoot_time', A.$('#shootTime').value || '');
    fd.append('location_source', geo.source);
    if (geo.lng !== null) { fd.append('longitude', geo.lng); fd.append('latitude', geo.lat); }
    fd.append('client_uuid', A.uuid());   // 幂等键：网络重试不会重复创建

    try {
      var res = await A.api('/reports', { method: 'POST', form: fd });
      A.Toast.success('上报成功，受理编号 ' + res.report_no);
      setTimeout(function () { location.href = 'report-detail.html?id=' + res.id; }, 800);
    } catch (e) {
      btn.disabled = false; btn.textContent = '提交上报';
      A.Toast.error(e.message);
    }
  };
})();
