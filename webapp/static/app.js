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
  progressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
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
  const sizeMB = (f.size / 1e6).toFixed(1);
  setStatus(`Загрузка 0% (${sizeMB} MB)…`);
  setProgress(0);

  const fd = new FormData();
  fd.append('file', f);
  fd.append('init_data', tg.initData);

  const xhr = new XMLHttpRequest();

  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable && e.total > 0) {
      const percent = Math.round((e.loaded / e.total) * 100);
      setProgress(percent);
      setStatus(`Загрузка ${percent}% (${sizeMB} MB)…`);
    } else {
      setStatus(`Загрузка… (${sizeMB} MB, прогресс недоступен)`);
    }
  });

  xhr.upload.addEventListener('load', () => {
    setProgress(100);
    setStatus(`Загрузка 100% (${sizeMB} MB), жду ответ сервера…`);
  });

  xhr.upload.addEventListener('error', () => {
    hideProgress();
    setStatus('❌ Ошибка сети при загрузке файла.', 'error');
    btn.disabled = false;
  });

  xhr.upload.addEventListener('abort', () => {
    hideProgress();
    setStatus('❌ Загрузка прервана.', 'error');
    btn.disabled = false;
  });

  xhr.addEventListener('load', () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      setProgress(100);
      setStatus('✅ Файл загружен — транскрипция идёт в чате.', 'success');
      setTimeout(() => tg.close(), 2000);
    } else {
      hideProgress();
      setStatus(`❌ Ошибка ${xhr.status}: ${xhr.responseText || '(пусто)'}`, 'error');
      btn.disabled = false;
    }
  });

  xhr.addEventListener('error', () => {
    hideProgress();
    setStatus('❌ Сеть оборвалась до ответа сервера.', 'error');
    btn.disabled = false;
  });

  xhr.addEventListener('timeout', () => {
    hideProgress();
    setStatus('❌ Таймаут запроса.', 'error');
    btn.disabled = false;
  });

  try {
    xhr.open('POST', '/api/upload');
    xhr.timeout = 0;
    xhr.send(fd);
  } catch (e) {
    hideProgress();
    setStatus(`❌ Не удалось отправить: ${e}`, 'error');
    btn.disabled = false;
  }
};
