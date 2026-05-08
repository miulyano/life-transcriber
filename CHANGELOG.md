# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Месячные лимиты часов транскрибации на отдельных пользователей через `bot/data/user_limits.json` (пользователи без записи имеют безлимит). Расход хранится в `data/usage.json` по календарным месяцам UTC. При превышении лимита бот предупреждает и блокирует обработку с указанием текущего лимита.
- Команда `/start` — приветствие и список доступных команд.
- Команда `/limit` — показывает текущий лимит, использовано и остаток на текущий месяц. Для пользователей без лимита отвечает «Лимит на транскрибации не установлен.»

## [1.0.0] – 2026-04-28

### Added

- Транскрибация голосовых сообщений и аудиофайлов Telegram через AssemblyAI Universal-2
- Транскрибация видеофайлов и video notes (извлечение аудио через FFmpeg)
- Акустическая диаризация спикеров с именованием и отображением прогресса в реальном времени
- GPT-4o анализ: адаптивный конспект по типу контента (лекция, интервью, монолог и др.)
- Разбивка монотранскрипта на смысловые абзацы через GPT
- Очистка и нормализация текста: кастомный словарь, word boost для ASR
- Распознавание ссылок: YouTube/RuTube/VK Video и другие (yt-dlp), Instagram Reels, Facebook, Yandex Disk, Yandex Music
- Загрузка Instagram и Facebook через встроенный Cobalt (с поддержкой cookies)
- Прямое скачивание публичных файлов с Yandex Disk через Cloud API
- Извлечение выпусков подкастов Yandex Music через RSS + yt-dlp
- Mini App (FastAPI + Caddy) для загрузки файлов из браузера напрямую в бот
- Whitelist-авторизация пользователей
- Docker Compose deployment (бот + Cobalt + webapp)

[1.0.0]: https://github.com/miulyano/life-transcriber/releases/tag/v1.0.0
