const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const fileInput = document.getElementById('file');
const btn = document.getElementById('upload');
const status = document.getElementById('status');
const progress = document.getElementById('progress');
const progressBar = document.getElementById('progress-bar');

function setStatus(msg, cls) {
  status.textContent = msg;
  status.className = cls || '';
}

function setProgress(percent) {
  progress.style.display = 'block';
  progressBar.style.width = `${percent}%`;
}

function hideProgress() {
  progress.style.display = 'none';
  progressBar.style.width = '0%';
}

btn.onclick = () => {
  const f = fileInput.files[0];
  if (!f) {
    setStatus('Выбери файл перед отправкой.');
    return;
  }

  btn.disabled = true;
  setStatus('Загрузка 0%…');
  setProgress(0);

  const fd = new FormData();
  fd.append('file', f);
  fd.append('init_data', tg.initData);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');

  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const percent = Math.round((e.loaded / e.total) * 100);
    setProgress(percent);
    setStatus(`Загрузка ${percent}%…`);
  };

  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      setProgress(100);
      setStatus('✅ Файл загружен — транскрипция идёт в чате.', 'success');
      setTimeout(() => tg.close(), 2000);
    } else {
      hideProgress();
      setStatus(`❌ Ошибка ${xhr.status}: ${xhr.responseText}`, 'error');
      btn.disabled = false;
    }
  };

  xhr.onerror = () => {
    hideProgress();
    setStatus('❌ Ошибка сети при загрузке.', 'error');
    btn.disabled = false;
  };

  xhr.send(fd);
};
