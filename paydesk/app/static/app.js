const feedback = document.querySelector('#feedback');
function flash(message, ok = false) {
  if (!feedback) return;
  feedback.textContent = message;
  feedback.classList.toggle('ok', ok);
}
document.querySelector('#refresh')?.addEventListener('click', () => location.reload());

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `请求失败 ${r.status}`);
  return data;
}

// 归档按钮：失败（阻断/事务回滚）时后端返回 409，页面保留原状态与旧版清单。
document.querySelector('#archive-btn')?.addEventListener('click', async (event) => {
  const btn = event.target;
  btn.disabled = true;
  try {
    const note = document.querySelector('#archive-note')?.value || '';
    const data = await post('/api/archive', { note });
    flash(`已生成归档版本 v${data.archived.version}，指纹 ${data.archived.seal.slice(0, 12)}…`, true);
    setTimeout(() => location.reload(), 900);
  } catch (error) {
    flash('归档被拒绝：' + error.message);
    btn.disabled = false;
  }
});
