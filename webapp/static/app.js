const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const fileInput = document.getElementById('file');
const timecodesInput = document.getElementById('timecodes');
const btn = document.getElementById('upload');
const status = document.getElementById('status');
const progress = document.getElementById('progress');
const progressBar = document.getElementById('progress-bar');

const tabUpload = document.getElementById('tab-upload');
const tabList = document.getElementById('tab-list');
const tabBtnUpload = document.getElementById('tab-btn-upload');
const tabBtnList = document.getElementById('tab-btn-list');
const listStatus = document.getElementById('list-status');
const transcriptsUl = document.getElementById('transcripts');

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

// --- Tabs ---

function activateTab(name) {
  const isUpload = name === 'upload';
  tabUpload.hidden = !isUpload;
  tabList.hidden = isUpload;
  tabBtnUpload.classList.toggle('active', isUpload);
  tabBtnList.classList.toggle('active', !isUpload);
  if (!isUpload) loadTranscripts();
}

tabBtnUpload.onclick = () => activateTab('upload');
tabBtnList.onclick = () => activateTab('list');

// --- API helper: initData goes in a header, never in the URL ---

function api(path, opts = {}) {
  return fetch(path, {
    ...opts,
    headers: {
      'X-Telegram-Init-Data': tg.initData,
      ...(opts.headers || {}),
    },
  });
}

// --- Transcripts list ---

function fmtDuration(sec) {
  const total = Math.round(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function confirmDialog(message) {
  return new Promise((resolve) => {
    if (tg.showConfirm) {
      tg.showConfirm(message, resolve);
    } else {
      resolve(window.confirm(message));
    }
  });
}

async function loadTranscripts() {
  listStatus.textContent = 'Загружаю…';
  transcriptsUl.innerHTML = '';
  try {
    const res = await api('/api/transcripts');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.items.length) {
      listStatus.textContent = 'Пока пусто. Отправь боту голосовое, видео или ссылку — запись появится здесь.';
      return;
    }
    listStatus.textContent = '';
    data.items.forEach((item) => transcriptsUl.appendChild(renderCard(item)));
  } catch (e) {
    listStatus.textContent = `❌ Не удалось загрузить список: ${e.message || e}`;
  }
}

function renderCard(item) {
  const li = document.createElement('li');
  li.className = 'card';

  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = item.title || 'Без названия';
  li.appendChild(title);

  const meta = document.createElement('div');
  meta.className = 'meta';
  const parts = [fmtDate(item.created_at)];
  if (item.duration_sec > 0) parts.push(fmtDuration(item.duration_sec));
  meta.textContent = parts.filter(Boolean).join(' · ');
  li.appendChild(meta);

  const actions = document.createElement('div');
  actions.className = 'actions';

  const resendBtn = document.createElement('button');
  resendBtn.textContent = '📨 Прислать в чат';
  resendBtn.onclick = () => resendTranscript(item.id, false, resendBtn);
  actions.appendChild(resendBtn);

  const resendTcBtn = document.createElement('button');
  resendTcBtn.textContent = '🕒 С таймкодами';
  resendTcBtn.onclick = () => resendTranscript(item.id, true, resendTcBtn);
  actions.appendChild(resendTcBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'danger';
  delBtn.textContent = '🗑';
  delBtn.title = 'Удалить';
  delBtn.onclick = async () => {
    const ok = await confirmDialog('Удалить запись? Файл будет удалён безвозвратно.');
    if (!ok) return;
    delBtn.disabled = true;
    try {
      const res = await api(`/api/transcripts/${item.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      li.remove();
      if (!transcriptsUl.children.length) {
        listStatus.textContent = 'Пока пусто. Отправь боту голосовое, видео или ссылку — запись появится здесь.';
      }
    } catch (e) {
      delBtn.disabled = false;
      listStatus.textContent = `❌ Не удалось удалить: ${e.message || e}`;
    }
  };
  actions.appendChild(delBtn);

  li.appendChild(actions);
  return li;
}

async function resendTranscript(id, timecoded, button) {
  button.disabled = true;
  try {
    const res = await api(`/api/transcripts/${id}/resend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timecoded }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    // Delivery continues in the background; progress shows up in the chat.
    listStatus.textContent = '📨 Отправляю в чат…';
    setTimeout(() => tg.close(), 800);
  } catch (e) {
    listStatus.textContent = `❌ Не удалось отправить: ${e.message || e}`;
    button.disabled = false;
  }
}

// --- Upload ---

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
  fd.append('timecodes', timecodesInput.checked ? 'true' : 'false');

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
      setStatus('✅ Файл загружен — транскрибация идёт в чате.', 'success');
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
