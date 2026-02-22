# Проверка ассетов и удаления сообщений

## Как проверить ассеты

Из корня проекта выполните:
```bash
python scripts/check_assets.py
```
Скрипт выведет, какие файлы в `assets/images/` найдены, какие отсутствуют.

---

## Список ассетов (что должен использовать код)

Папка по умолчанию: **`assets/images/`** (относительно корня проекта).

### Новые/обновлённые игры

| Файл | Игра | Назначение |
|------|------|------------|
| `echo.jpg` | /echo | Одна картинка при результате «кто ты» |
| `random_win.jpg` | /random | Запас при выигрыше, если у выбранной игры нет своего win |
| `random_lose.jpg` | /random | Запас при проигрыше |
| `gamerandom_load.jpg` | /gamerandom | Опционально: загрузка «Матрица думает» |
| `blackmarket_start.jpg` | /blackmarket | Старт с кнопками сделок |
| `blackmarket_win.jpg` | /blackmarket | Победа по сделке |
| `blackmarket_scam.jpg` | /blackmarket | Скам / проигрыш |
| `fracture.jpg` | /fracture | Старт теста (первый вопрос) — по конвенции: `fracture` + start = fracture.jpg |
| `fracturewin.jpg` | /fracture | Победа в тесте |
| `fracturelose.jpg` | /fracture | Проигрыш в тесте |
| `mirror.jpg` | /mirror | Старт (три кнопки: в себя / в противника / ящик) |
| `mirror_box.jpg` | /mirror | Открытый ящик, «подглядеть патрон» |
| `mirrorwin.jpg` | /mirror | Победа |
| `mirrorlose.jpg` | /mirror | Поражение |

### /random — картинки выбранной игры

Если в раунд выбрана игра `slot`, `konopla`, `kripta`, `almaz`, `perekyp` или одна из risk40, код ищет:
- при выигрыше: `<game_id>win.jpg` (например `slotwin.jpg`, `kriptawin.jpg`);
- при проигрыше: `<game_id>lose.jpg`.

Если файла нет — подставляются `random_win.jpg` / `random_lose.jpg`.

---

### Полная таблица: все игры и файлы картинок

| Игра | Файлы в `assets/images/` | Примечание |
|------|--------------------------|------------|
| **slot** | `5.jpg` (выигрыш), `1.jpg`–`4.jpg` (проигрыш) | Свои имена, не конвенция win/lose |
| **konopla** | `kon.jpg` (старт/общее), `konwin.jpg` (выигрыш) | Проигрыш — текст |
| **kripta** | `Startkripta.jpg` или `kripta.jpg` или `1.jpg` (старт), `kriptawin.jpg`, `kriptalox.jpg` (обвал) | |
| **almaz** | `almaz.jpg` (старт), `almazwin.jpg`, `almazlox.jpg` | |
| **chisla** | `chisla.jpg`, `winchisla.jpg`, `loxchislo.jpg` | |
| **rulet** | `rulet.jpg` (старт), `rulet_out.jpg`, `rulet_win.jpg` | |
| **frekaz** | `frekaz.jpg`, `frekaz_win.jpg` | |
| **perekyp** | `perekup.jpg`, `perekupwin.jpg`, `perekuplose.jpg`, `perekuptorg.jpg` | |
| **risk40** (reactor, vault, …) | `<slug>.jpg`, `<slug>win.jpg`, `<slug>lose.jpg` | Например `reactor.jpg`, `reactorwin.jpg`, `reactorlose.jpg` |
| **random** | `random_win.jpg`, `random_lose.jpg` + картинки выбранной игры | |
| **gamerandom** | `gamerandom_load.jpg` (опционально) | |
| **blackmarket** | `blackmarket_start.jpg`, `blackmarket_win.jpg`, `blackmarket_scam.jpg` | |
| **echo** | `echo.jpg` | |
| **fracture** | `fracture.jpg`, `fracturewin.jpg`, `fracturelose.jpg` | Конвенция: slug + win/lose |
| **mirror** | `mirror.jpg`, `mirror_box.jpg`, `mirrorwin.jpg`, `mirrorlose.jpg` | |
| **plsdon** | `jail.jpg`, `otzhal.jpg`, `beg.jpg` | |

---

### Исправления отправки ассетов (сделано)

- **blackmarket:** при ошибке edit результат теперь отправляется с картинкой (`send_photo`), если есть `blackmarket_win.jpg` / `blackmarket_scam.jpg`.
- **fracture:** результат (win/lose) теперь сначала пытаем показать через `edit_message_media` (картинка + подпись), при ошибке — `send_photo` / `send_message`.
- **risk40 (забрать):** при ошибке edit выигрыш отправляется с картинкой `send_photo(slugwin.jpg)`.
- **risk40 (обвал):** при ошибке edit проигрыш отправляется с картинкой `send_photo(sluglose.jpg)`.

---

## Удаление сообщений (аудит)

Таймаут задаётся в конфиге: **`GAME_RESULT_DELETE_TIMEOUT`** (по умолчанию 20 сек).

### Логика в коде

- **delete_message_after(message, sec)** — удаляет переданное сообщение через `sec` секунд.
- **delete_message_after_by_id(bot, chat_id, message_id, sec)** — удаляет сообщение по id (удобно, когда сообщение могло быть заменено на новое при ошибке edit).

### По играм

| Игра | Что удаляется | Через сколько | Замечание |
|------|----------------|---------------|-----------|
| **/random** | 1) Сообщение загрузки «Разлом произошёл» 2) Сообщение с результатом (картинка + текст) | 20 сек | Оба планируются — ок. |
| **/gamerandom** | Сообщение загрузки (оно же редактируется в результат) | 20 сек | Ок. |
| **/blackmarket** | Сообщение с результатом. Если edit не удался — отправляется новое, удаляется **id нового** сообщения | 20 сек | Ок (исправлено ранее). |
| **/echo** | Сообщение с результатом «кто ты» (то же, что «Система думает» после edit_caption) | 20 сек | Ок. |
| **/fracture** | 1) Сообщение загрузки при наличии стартовой картинки — через 3 сек 2) Сообщение с результатом (после 10 вопросов) | 3 сек / 20 сек | Ок. |
| **/mirror** | Сообщение с результатом. Если edit не удался — отправляется новое, удаляется **id того сообщения, где показан результат** (либо отредактированное, либо новое) | 20 сек | Ок (исправлено в этой проверке). |

### Итог по удалению

- Для всех перечисленных игр результат (или единственное итоговое сообщение) планируется на удаление через 20 сек.
- В **blackmarket** и **mirror** при fallback на отправку нового сообщения удаляется именно сообщение с результатом (в mirror исправлено: раньше при новом сообщении не планировалось его удаление).

---

## Что есть / чего нет (заполнить после запуска скрипта)

После выполнения `python scripts/check_assets.py` в консоли будет список **OK** (есть) и **--** (нет). Перенесите сюда или сохраните вывод скрипта.

- Если папки `assets/images` нет — создайте её и положите нужные файлы.
- Отсутствующие файлы не ломают бота: код проверяет `.exists()` и при отсутствии картинки отправляет только текст.

---

## Краткий вывод

1. **Ассеты:** все пути заданы в коде и в скрипте `scripts/check_assets.py`. Запустите скрипт локально, чтобы увидеть, чего не хватает.
2. **Удаление сообщений:** для /random, /gamerandom, /blackmarket, /echo, /fracture, /mirror логика удаления корректна; для mirror при отправке нового сообщения вместо edit теперь тоже планируется удаление этого сообщения через 20 сек.
