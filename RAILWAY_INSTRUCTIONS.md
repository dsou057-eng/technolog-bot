# Как выложить бота Tehnolog Games на Railway — пошагово

## Что нужно заранее

- Аккаунт на [railway.app](https://railway.app) (логин через GitHub).
- Репозиторий проекта на GitHub (тот же, что у тебя в папке `my_bot`).

---

## Шаг 1. Залить код в GitHub

Открой терминал в папке проекта (`c:\Users\dsou0\Desktop\my_bot`) и выполни:

```bash
git add .
git status
```

Убедись, что в списке есть все нужные папки: `handlers/`, `services/`, `games/`, `main.py`, `config.py`, `db.py`, `requirements.txt` и т.д.

```bash
git commit -m "Deploy Tehnolog Games to Railway"
git push origin main
```

Если репозиторий ещё не привязан к GitHub:

1. Зайди на [github.com](https://github.com) → New repository.
2. Создай репозиторий (например, `tehnolog-games-bot`), **не** добавляй README/.gitignore — репо должно быть пустым.
3. В папке проекта выполни (подставь свой логин и имя репо):

```bash
git remote add origin https://github.com/ТВОЙ_ЛОГИН/tehnolog-games-bot.git
git branch -M main
git push -u origin main
```

---

## Шаг 2. Создать проект на Railway

1. Зайди на [railway.app](https://railway.app) и войди через GitHub.
2. Нажми **«New Project»**.
3. Выбери **«Deploy from GitHub repo»**.
4. Укажи репозиторий с ботом (например, `tehnolog-games-bot`).
5. Railway сам создаст сервис и начнёт сборку — пока можно не трогать настройки.

---

## Шаг 3. Указать переменные окружения (обязательно)

1. В проекте Railway открой свой сервис (один блок/карточка).
2. Перейди во вкладку **«Variables»** (или **«Settings»** → **Variables**).
3. Нажми **«Add Variable»** или **«RAW Editor»** и добавь переменные по одной или списком.

**Минимум для работы бота:**

| Переменная   | Значение                    | Описание |
|-------------|-----------------------------|----------|
| `BOT_TOKEN` | твой токен от @BotFather    | Обязательно. Формат: `123456789:ABCdef...` |
| `ENVIRONMENT` | `prod`                    | Чтобы бот включил webhook на Railway. |

**Пример (подставь свой токен):**

```
BOT_TOKEN=1234567890:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ENVIRONMENT=prod
```

Остальные переменные не обязательны: порт и webhook URL бот подхватит сам (Railway задаёт `PORT` и при генерации домена — `RAILWAY_PUBLIC_DOMAIN`).

---

## Шаг 4. Команда запуска

1. В сервисе открой **«Settings»** (или вкладку с настройками сервиса).
2. Найди блок **«Build»** / **«Deploy»** или **«Start Command»** / **«Run Command»**.
3. Команда запуска должна быть:

```bash
python main.py
```

Если в Railway есть поле **«Build Command»** — можно оставить пустым или указать:

```bash
pip install -r requirements.txt
```

Если используется **Nixpacks** (по умолчанию), Railway сам установит зависимости из `requirements.txt` и запустит приложение. Тогда важно, чтобы в настройках сервиса в **«Start Command»** было:

```bash
python main.py
```

(Иногда поле называется **«Custom Start Command»** или **«Run»**.)

---

## Шаг 5. Публичный домен (для webhook)

Чтобы Telegram мог слать обновления боту, нужен HTTPS-адрес.

1. В настройках сервиса найди раздел **«Networking»** / **«Settings»**.
2. Нажми **«Generate Domain»** (или **«Add Domain»**).
3. Railway выдаст адрес вида: `твой-проект.up.railway.app`.

После этого бот сам соберёт webhook URL из переменной `RAILWAY_PUBLIC_DOMAIN` (её Railway подставляет автоматически), если у тебя задано `ENVIRONMENT=prod`. Дополнительно указывать `WEBHOOK_URL` не обязательно.

Если захочешь задать URL вручную — добавь переменную:

```
WEBHOOK_URL=https://твой-проект.up.railway.app/webhook
```

(замени `твой-проект` на свой домен из Railway).

---

## Шаг 6. Проверка деплоя

1. Дождись окончания деплоя (статус **«Success»** / зелёная галочка).
2. Открой вкладку **«Logs»** (логи сервиса).
3. В логах должно быть что-то вроде:

```
Бот запущен: @ТвойБот
ID бота: ...
Регистрация роутеров завершена
Роутер base зарегистрирован
...
Бот готов к работе!
```

4. Напиши боту в Telegram команду `/start`. Если он ответил — бот выложен и работает.

Если в логах есть **«Модуль handlers.base не найден»** — на сервер попал неполный код. Проверь, что в репозитории есть папка `handlers/` со всеми файлами и что при деплое не используется `.dockerignore`, который исключает эти папки.

---

## Краткий чеклист

- [ ] Код запушен в GitHub (`git add .` → `git commit` → `git push`).
- [ ] На Railway создан проект из этого репозитория.
- [ ] В Variables заданы `BOT_TOKEN` и `ENVIRONMENT=prod`.
- [ ] У сервиса сгенерирован домен (Generate Domain).
- [ ] Start command: `python main.py`.
- [ ] В логах нет ошибок, бот отвечает на `/start`.

---

## Если бот не отвечает

- Проверь логи на ошибки (красные строки).
- Убедись, что `BOT_TOKEN` скопирован без пробелов и лишних символов.
- Убедись, что домен сгенерирован и в логах есть строка про webhook (например, «Webhook установлен» или «Режим webhook»).
- Проверь, что не запущен второй экземпляр бота локально (иначе будет конфликт с Telegram API).

Готово. После этих шагов бот работает на сервере 24/7.
