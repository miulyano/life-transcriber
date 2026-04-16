# life-transcriber

Персональный Telegram-бот для транскрибации аудио и видео через OpenAI Whisper,
с генерацией краткого конспекта на GPT-4o.

## Что умеет

- 🎙 **Голосовые сообщения** — транскрибация в текст
- 🎥 **Видео-кружочки** (video notes) — транскрибация аудиодорожки
- 📼 **Видео-файлы** (`.mp4`, `.mov` и т.п., в том числе пересланные) — извлечение аудио + транскрибация; файлы до ~2 GB (с локальным Bot API)
- 🔗 **Ссылки на видео** — YouTube, RuTube, VK Video, Vimeo и [многие другие платформы](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) (всё, что поддерживает `yt-dlp`)
- ☁️ **Публичные ссылки на Яндекс Диск** — аудио или видео-файлы вида `https://disk.yandex.ru/d/...` и `https://yadi.sk/d/...` качаются напрямую через публичный Cloud API (авторизация не требуется)
- 📝 **Краткий конспект** — inline-кнопка под транскрипцией, генерирует тезисы через GPT-4o
- ⏳ **Интерактивный статус** — во время обработки присылается одно сообщение с анимированным прогресс-баром и фазами («Скачиваю…» → «Транскрибирую…»); сообщение удаляется, когда приходит транскрипция, или превращается в текст ошибки, если что-то сломалось

**Формат ответа:**
- Короткий текст (≤ 2000 символов) — приходит прямо в чате
- Длинный текст — приходит файлом `transcript.txt` с форматированием от Whisper
  (абзацы, пунктуация)

**Доступ:** по whitelist Telegram user ID. Случайные пользователи не получают ответа.

## Стек

- Python 3.11+
- [aiogram 3.x](https://github.com/aiogram/aiogram) — async Telegram Bot framework
- [OpenAI API](https://platform.openai.com/docs/api-reference) — Whisper + GPT-4o
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — скачивание с видео-платформ
- [FFmpeg](https://ffmpeg.org/) — извлечение аудио из видео
- [telegram-bot-api (local)](https://github.com/tdlib/telegram-bot-api) — локальный сервер Bot API, снимает лимит 20 MB на скачивание файлов
- Docker + Docker Compose

## Как развернуть свой

### 1. Форкни или склонируй репозиторий

```bash
git clone https://github.com/USER/life-transcriber.git
cd life-transcriber
```

### 2. Получи необходимые ключи и ID

**Telegram bot token:**
1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`, следуй инструкциям
3. Сохрани токен вида `1234567890:ABCdef...`

**OpenAI API key:**
1. Зайди на https://platform.openai.com/api-keys
2. Создай новый ключ — начинается с `sk-...` или `sk-proj-...`
3. Пополни баланс на https://platform.openai.com/account/billing
   (минимум $5 — без баланса API не работает)

**Telegram user ID:**
1. Напиши боту [@userinfobot](https://t.me/userinfobot) команду `/start`
2. Он пришлёт твой ID (число типа `123456789`)
3. Повтори для каждого, кому даёшь доступ

**Telegram api_id и api_hash** (для локального Bot API, снимает лимит 20 MB):
1. Открой https://my.telegram.org/auth и войди под своим номером телефона
2. Перейди в «API development tools»
3. Нажми «Create application» — название и платформа произвольные
4. Сохрани `App api_id` (число) и `App api_hash` (hex-строка)

### 3. Настрой `.env`

```bash
cp .env.example .env
```

Открой `.env` и заполни:

```env
BOT_TOKEN=1234567890:ABCdef...
OPENAI_API_KEY=sk-proj-...
ALLOWED_USER_IDS=123456789,987654321

# Локальный Bot API (файлы до ~2 GB)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abc123def456...
TELEGRAM_API_URL=http://tg-api:8081
```

Опциональные переменные (дефолты в `.env.example`):
- `LONG_TEXT_THRESHOLD=2000` — порог длины текста, после которого ответ идёт файлом
- `WHISPER_MODEL=whisper-1` — модель транскрибации
- `GPT_MODEL=gpt-4o` — модель для конспекта
- `TEMP_DIR=/tmp/transcriber` — где хранить временные файлы

### 4. Запусти через Docker

```bash
docker compose up -d --build
```

При первом запуске `tg-api` инициализируется ~15–20 сек, затем бот поднимается автоматически.

Проверь логи:
```bash
docker compose logs -f
```

Должно быть:
```
tg-api  | Local Bot API server started
bot     | Bot started. Allowed users: [...]
bot     | Start polling
bot     | Run polling for bot @YourBotName
```

### 5. Используй

Напиши своему боту в Telegram:
- Запиши **голосовое** → получи текст
- Запиши **кружочек** → получи текст
- Отправь **видео** (как файл или пересланное, до ~2 GB) → получи текст
- Вставь **ссылку на видео** → получи текст
- Вставь **публичную ссылку на аудио/видео в Яндекс Диске** → получи текст
- Под любой транскрипцией нажми **«📝 Краткий конспект»** → получи тезисы

## Деплой на VPS

На сервере с Ubuntu 22.04+:

```bash
# Установка Docker (если ещё нет)
apt update && apt install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Деплой
git clone https://github.com/USER/life-transcriber.git /opt/life-transcriber
cd /opt/life-transcriber
# Создай .env с реальными ключами (НЕ коммить его!)
docker compose up -d --build
```

Обновление после изменений:
```bash
git pull && docker compose up -d --build
```

### Диск и очистка

Локальный Bot API сохраняет принятые файлы в volume `tg-api-data`. Сервис
`tg-api-cleanup` автоматически удаляет файлы старше 3 дней раз в сутки.

Полная ручная очистка (только пока бот остановлен):
```bash
docker compose down
docker volume rm life-transcriber_tg-api-data
docker compose up -d
```

## Тесты

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

Покрыта ключевая логика: парсинг конфига, whitelist-авторизация, кэш текстов с TTL,
порог inline/file, URL regex, прогресс-бар, транскрибер.

## Структура проекта

```
life-transcriber/
├── bot/
│   ├── main.py                  # Точка входа; поддержка локального Bot API
│   ├── config.py                # Pydantic Settings (читает .env)
│   ├── handlers/
│   │   ├── voice.py             # voice + video_note (кружочки)
│   │   ├── video.py             # видео-файлы и document/video
│   │   ├── links.py             # URL → yt-dlp → transcribe
│   │   └── callbacks.py         # кнопка «Краткий конспект»
│   ├── services/
│   │   ├── transcriber.py       # OpenAI Whisper + автосплит файлов > 24MB
│   │   ├── summarizer.py        # OpenAI GPT-4o → конспект
│   │   ├── yandex_disk.py       # Публичное API Яндекс Диска
│   │   └── downloader.py        # Диспетчер: Яндекс Диск / yt-dlp + FFmpeg
│   ├── middlewares/auth.py      # Whitelist Telegram user ID
│   └── utils/
│       ├── text.py              # reply_text_or_file + кэш хэшей с TTL 10 мин
│       └── progress.py          # ProgressReporter: один статус с анимированным баром
├── tests/                       # pytest
├── Dockerfile
├── docker-compose.yml           # 3 сервиса: tg-api, tg-api-cleanup, bot
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

## Правила разработки

См. [CLAUDE.md](CLAUDE.md) — правила прогона тестов и работы с тестами при изменениях.

## Безопасность

- `.env` с реальными ключами **никогда** не коммитится (уже в `.gitignore`)
- В репозиторий попадает только `.env.example` с плейсхолдерами
- `api_id` и `api_hash` привязаны к твоему Telegram-аккаунту — храни в `.env`, не публикуй
- Доступ к боту — только по whitelist Telegram user ID в `ALLOWED_USER_IDS`
- Пользователи вне whitelist не получают ответа (бот молчит, не раскрывает своё существование)
- Порт `tg-api` (8081) не публикуется наружу — доступен только внутри docker-сети

## Лицензия

Личный проект. Форкай, модифицируй, используй — но ответственность за API-расходы
и сохранность ключей на тебе.
