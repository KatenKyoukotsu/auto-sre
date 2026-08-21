# Редизайн фронтенда: разделение front/back

Статус: в работе. Фазы 1–4 готовы, далее фаза 5. Каждая фаза независимо
проверяется человеком в браузере.

## Цель

Убрать Jinja2-шаблонизацию из sre-agent: бэкенд становится чистым JSON API,
фронтенд — статические файлы (HTML/CSS/JS) без этапа сборки. Любой файл можно
проверить глазами; правка CSS/JS видна по F5 (dev-compose монтирует исходники).

## Решения (согласованы)

| Вопрос | Решение |
|---|---|
| Топология | Каталог `files/frontend/` в репо; копируется в образ sre-agent |
| Сборка | Нет (нативные ES-модули); marked.js + DOMPurify вендорятся в `vendor/` |
| Typewriter | Только для постов, появившихся пока страница открыта (id vs sessionStorage); клик — пропустить |
| Живая стена | Поллинг `/api/findings?limit=100` каждые ~15 c, diff по id, без reload |
| Auth | Роуты `/` и `/blog` остаются за Basic Auth middleware (FileResponse, не в exclude-листе); `/static` публичный — данных не содержит |
| Даты | Локальный часовой пояс браузера (Intl), вместо сырого UTC ISO |
| Кэш HTML | no-cache, чтобы правки были видны сразу |

## Структура

```
files/frontend/
├── index.html          # стена — статическая оболочка
├── blog.html
├── favicon.svg
├── css/
│   ├── tokens.css      # :root-переменные + тайминги/изинги анимаций
│   ├── base.css        # топбар, карточки, формы
│   └── animations.css  # вся хореография в одном файле
├── js/
│   ├── api.js          # все fetch() в одном месте
│   ├── format.js       # даты, проценты, экранирование
│   ├── markdown.js     # обёртка marked + DOMPurify
│   ├── effects.js      # typewriter, тосты, скелетоны
│   ├── wall.js         # состояние стены + рендер + поллинг
│   └── blog.js
└── vendor/             # marked.min.js, purify.min.js (прод офлайн)
```

## Изменения бэкенда (минимальные)

- Контекст сборки sre-agent → корень `files/` (`dockerfile: sre-agent/Dockerfile`) —
  тот же паттерн, что у alert-analyzer; Dockerfile делает `COPY frontend/ ./static/`
- `tasks/main.yml`: копирование `files/frontend/` → `/opt/docker/auto-sre/frontend/`
- Dev-compose: маунт `./files/frontend:/app/static:cached`
- Фаза 3: роуты `/` и `/blog` → FileResponse; удаление `templates/`, старого `static/`,
  зависимостей jinja2 и markdown
- Новые эндпоинты **не нужны**: статус-бар — существующий `/api/health`
  (model, last_scan, last_error), поллинг — существующий `/api/findings`

## Анимации

| Эффект | Было | Станет |
|---|---|---|
| Typewriter | при каждой загрузке, блокирует чтение | только для «живых» новых постов, пропускается кликом |
| Ack карточки | мгновенная прозрачность | плавный transition |
| Новый скан | `location.reload()` | тост + карточки въезжают со stagger (`--i`) |
| Загрузка данных | пустой миг | скелетоны (переиспользуем shimmer) |
| Кнопки | без обратной связи | спиннер внутри кнопки на время запроса |
| Reduced motion | игнорируется | `@media (prefers-reduced-motion)` отключает всё |

Тайминги — токены в tokens.css (`--dur-fast: 150ms` и т.д.). JS только вешает
классы, хореография целиком в CSS.

## XSS

Контент блога — вывод LLM (произвольный текст). Обязательный прогон через
DOMPurify перед вставкой в DOM.

## Фазы

1. **Каркас**: контекст сборки `./files`, маунты, `frontend/` с favicon в образе.
   Поведение UI не меняется — ✅
2. **Экстракция**: JS из шаблонов → `js/wall.js`, `js/blog.js`; CSS → `css/*`.
   Jinja остаётся только для циклов данных. Поведение 1-в-1 — ✅
3. **Отказ от Jinja**: FileResponse + клиентский рендер из JSON, удаление шаблонов
   и зависимостей. Проверка: страницы за auth рендерят те же данные — ✅
4. **UX**: живой поллинг стены (diff по id, новые карточки с `.card-enter`),
   тосты (`effects.js`), скелетоны загрузки, спиннеры в кнопках,
   тост по завершении генерации блога (успех/ошибка) — ✅
5. **Анимации**: typewriter-для-новых, stagger, reduced-motion
6. *(опционально)* SSE вместо поллинга, View Transitions

## Верификация

- Каждый шаг: Playwright-снапшоты против текущего поведения
  (`npx playwright-cli open/goto/snapshot/click`)
- API: curl (`/api/health`, `/api/findings`, `/metrics`)
- Prod-шаблон: `docker compose config` на отрендеренной копии
