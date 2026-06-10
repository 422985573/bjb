/* eslint-disable no-unused-vars */
function whStepAdjust(btn, dir) {
  var group = btn.closest('.wh-adjust-input-group') || btn.closest('.wse-adjust-row');
  var input = group.querySelector('.wh-adjust-input');
  if (!input) return;
  var val = parseFloat(input.value) || 0;
  input.value = val + dir;
}

function doAdjust(btn) {
  var key = btn.dataset.key;
  var card = btn.closest('.wh-sheet-card') || btn.closest('.wse-adjust-row') || btn.parentElement;
  var input = card.querySelector('.wh-adjust-input') || card.querySelector('.wse-adjust-input');
  var result = card.querySelector('.wh-adjust-result');
  var pct = parseFloat(input.value);

  if (isNaN(pct) || pct === 0) {
    result.className = 'wh-adjust-result error';
    result.textContent = '请输入非零数值';
    return;
  }

  if (!confirm('确认对该渠道所有价格' + (pct > 0 ? '上浮' : '下调') + ' ' + Math.abs(pct) + '%？此操作不可撤销。')) {
    return;
  }

  btn.disabled = true;
  result.className = 'wh-adjust-result';
  result.textContent = '处理中...';

  fetch('/api/warehouse-adjust-price', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sheet_key: key, percentage: pct })
  })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      btn.disabled = false;
      if (res.success) {
        result.className = 'wh-adjust-result';
        result.textContent = '已调整 ' + (res.adjusted_count || 0) + ' 个价格';
        input.value = '';
      } else {
        result.className = 'wh-adjust-result error';
        result.textContent = res.message || '调价失败';
      }
    })
    .catch(function () {
      btn.disabled = false;
      result.className = 'wh-adjust-result error';
      result.textContent = '网络错误';
    });
}
