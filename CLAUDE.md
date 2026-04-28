# life-transcriber — правила разработки

## Тесты

**Всегда прогонять `pytest` перед финализацией новой фичи и особенно перед `git push`.**

```bash
source .venv/bin/activate
pytest -v
```

Если хотя бы один тест упал — **не пушить и не закрывать задачу**, пока всё не станет зелёным.

**Никогда не править тесты, чтобы они проходили.** Если тест упал — сломана логика в
основном коде, чинить нужно код, а не тест. Тесты — источник правды о том, как должна
работать ключевая логика (парсинг конфига, whitelist, кэш, порог inline/file, URL regex).

**Исключения, когда тест разрешено менять:**

1. Пользователь явно сказал, что поменялись требования к поведению (например, «теперь
   ALLOWED_USER_IDS должен поддерживать ещё и пробелы как разделители»). В этом случае
   сначала обновляются тесты под новое поведение, потом код — и тесты снова должны
   пройти.

2. Поменялась логика/флоу и тест ссылается на то, чего больше нет в коде: функция
   удалена, переименована, сигнатура изменилась, флоу переработан. В этом случае тест
   нужно переписать под новый код (или удалить, если покрытый кейс больше не актуален).
   Но прежде чем править тест таким образом — убедиться, что изменение в коде
   действительно намеренное, а не регрессия.

## README

**При любых изменениях в архитектуре — проверять и актуализировать `README.md`
перед `git push`.**

Изменения, требующие обновления README:
- Добавлены/удалены/переименованы модули или директории в `bot/` → обновить раздел
  «Структура проекта»
- Изменился стек (новая библиотека, смена фреймворка) → обновить раздел «Стек»
- Появились/исчезли env-переменные → обновить раздел про настройку `.env`
- Изменился флоу деплоя, команды запуска, требования к системе → обновить разделы
  «Как развернуть свой» и «Деплой на VPS»
- Появились новые фичи или типы поддерживаемых входов → обновить раздел «Что умеет»
- Изменилась команда запуска тестов или их структура → обновить раздел «Тесты»

Перед пушем: просканировать README и убедиться, что все команды, пути, имена файлов
и описания соответствуют актуальному состоянию кода. Устаревший README хуже,
чем его отсутствие — он вводит в заблуждение.

## Git workflow

**Никогда не коммитить напрямую в `main`.** Любое изменение — через ветку + PR.

### Старт работы (каждый раз, когда начинается задача)

Если текущая ветка `main` и рабочее дерево чистое:
```bash
git checkout main && git pull
git checkout -b <type>/<kebab-case-description>
```

### Имена веток

| Тип | Когда | Пример |
|---|---|---|
| `feat/` | Новая фича для пользователя | `feat/yandex-disk-upload` |
| `fix/` | Исправление бага | `fix/whisper-chunk-leak` |
| `refactor/` | Внутренние изменения без смены поведения | `refactor/split-downloader` |
| `docs/` | Только документация | `docs/env-vars-reference` |
| `test/` | Только тесты | `test/callback-handlers` |
| `chore/` | Зависимости, конфиги, CI | `chore/bump-aiogram` |
| `perf/` | Оптимизация производительности | `perf/cache-transcriptions` |

Имена короткие, в нижнем регистре через дефис.

### Commit messages — Conventional Commits

Формат: `<type>(<scope>): <imperative description>`

- `scope` опционален: модуль/область (`handlers`, `config`, `transcriber`)
- Описание — в императиве, без точки в конце, до ~70 символов
- Тело (через пустую строку) — объясняет **почему**, не **что**

Примеры:
```
feat(handlers): add Yandex Disk link support
fix(transcriber): release temp chunk files on exception
docs: update README with deploy verification steps
refactor(config): move defaults to a typed Defaults class
test(text): cover TTL boundary at exactly CACHE_TTL
chore: bump aiogram to 3.15.1
```

Типы: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`.

### Перед открытием PR

1. Прогнать тесты: `source .venv/bin/activate && pytest -v` — все должны быть зелёные
2. Сверить `README.md` с текущим состоянием кода (см. раздел «README»)
3. `git push -u origin <branch>`

### Открытие PR через `gh`

```bash
gh pr create --title "<type>(<scope>): краткое описание" --body "$(cat <<'EOF'
## Summary
- <что и зачем>

## Test plan
- [ ] pytest passes
- [ ] <manual checks, если нужны>
EOF
)"
```

PR-титл и тело должны соответствовать Conventional Commits, т.к. они станут текстом
squash-коммита в `main` после мерджа (так настроен репо).

### Мердж

Только **squash merge**. Merge commits и rebase merges запрещены (настроено в репо):
```bash
gh pr merge --squash --delete-branch
```

Ветка удалится на GitHub автоматически. Локально — вернуться на main и удалить ветку:
```bash
git checkout main && git pull
git branch -d <branch>
```

### Итог

`git log main --oneline` должен быть прямой вертикалью из Conventional Commits-титлов.
Никаких merge commits, никаких WIP'ов, никаких веток в истории main.

## Версионирование и CHANGELOG

Проект использует **Semantic Versioning** (SemVer): `MAJOR.MINOR.PATCH`.

| Тип изменения | Bump |
|---|---|
| `fix` — исправление бага | PATCH |
| `feat` — новая функция | MINOR |
| Ломающее изменение API/поведения | MAJOR |

### Правила для каждого PR

**Любой PR, кроме `docs/` и `chore/`, должен:**

1. Обновить файл `VERSION` (поднять нужный компонент).
2. Добавить запись в раздел `## [Unreleased]` файла `CHANGELOG.md`.
3. Обновить номер версии в бейдже в `README.md` (строка `img.shields.io/badge/version-...`).

Формат записи в changelog:

```
### Added / Fixed / Changed / Removed
- Краткое описание изменения (максимум 1–2 строки)
```

### Выпуск нового релиза

1. В `CHANGELOG.md` переименовать `## [Unreleased]` → `## [x.y.z] – YYYY-MM-DD`
   и добавить новый пустой `## [Unreleased]` выше.
2. Обновить `VERSION` до финального номера.
3. Обновить бейдж в `README.md`.
4. Прогнать тесты, открыть PR.
5. После мерджа: `git tag vX.Y.Z && git push origin vX.Y.Z`

### Пример цикла (patch)

```bash
# В процессе работы над fix-PR:
echo "1.0.1" > VERSION
# В CHANGELOG.md добавить под [Unreleased]:
# ### Fixed
# - Исправлен лимит GPT-токенов в split_into_paragraphs

# Перед релизом — переименовать заголовок:
# [Unreleased] → [1.0.1] – 2026-05-15
# добавить новый ## [Unreleased] выше

# После мерджа в main:
git tag v1.0.1 && git push origin v1.0.1
```

## Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
