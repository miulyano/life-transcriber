const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const fileInput = document.getElementById('file');
const btn = document.getElementById('upload');
const status = document.getElementById('status');

function setStatus(msg, cls) {
  status.textContent = msg;
  status.className = cls || '';
}

btn.onclick = async () => {
  const f = fileInput.files[0];
  if (!f) {
    setStatus('Выбери файл перед отправкой.');
    return;
  }

  btn.disabled = true;
  const sizeMB = (f.size / 1e6).toFixed(1);
  setStatus(`Загружаю ${sizeMB} MB и транскрибирую…\nЭто может занять несколько минут для больших файлов.`);

  const fd = new FormData();
  fd.append('file', f);
  fd.append('init_data', tg.initData);

  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    if (r.ok) {
      setStatus('✅ Файл принят — транскрипция идёт в фоне.\nРезультат придёт в чат бота.', 'success');
      setTimeout(() => tg.close(), 3000);
    } else {
      const err = await r.text();
      setStatus(`❌ Ошибка ${r.status}: ${err}`, 'error');
      btn.disabled = false;
    }
  } catch (e) {
    setStatus(`❌ Ошибка сети: ${e}`, 'error');
    btn.disabled = false;
  }
};
