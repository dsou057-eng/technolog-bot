# Ассеты и игры — текущее состояние

## Какие ассеты НУЖНЫ сейчас

Бот использует эти файлы. Если файла нет — отправляется только текст (без падения).

### Изображения (папка `assets/images/`)

| Файл | Где используется |
|------|------------------|
| **Игры (основные)** | |
| 1.jpg, 2.jpg, 3.jpg, 4.jpg, 5.jpg | /slot (проигрыш 1–4, выигрыш 5) |
| kon.jpg, konwin.jpg | /konopla |
| Startkripta.jpg, kripta.jpg, kriptalox.jpg, kriptawin.jpg | /kripta (Lucky Jet) |
| almaz.jpg, almazwin.jpg, almazlox.jpg | /almaz |
| jail.jpg, otzhal.jpg, beg.jpg | /plsdon |
| chisla.jpg, winchisla.jpg, loxchislo.jpg | /chisla |
| **40 играбельных игр (риск/забрать)** | |
| Для каждой игры нужны **3 файла**: `<slug>.jpg` (старт), `<slug>win.jpg` (выигрыш), `<slug>lose.jpg` (проигрыш). |
| Список ниже — все 120 файлов для 40 игр. | |
| reactor.jpg, reactorwin.jpg, reactorlose.jpg | /reactor |
| vault.jpg, vaultwin.jpg, vaultlose.jpg | /vault |
| dicepath.jpg, dicepathwin.jpg, dicepathlose.jpg | /dicepath |
| overheat.jpg, overheatwin.jpg, overheatlose.jpg | /overheat |
| mindlock.jpg, mindlockwin.jpg, mindlocklose.jpg | /mindlock |
| bombline.jpg, bomblinewin.jpg, bomblinelose.jpg | /bombline |
| liftx.jpg, liftxwin.jpg, liftxlose.jpg | /liftx |
| doza.jpg, dozawin.jpg, dozalose.jpg | /doza |
| shum.jpg, shumwin.jpg, shumlose.jpg | /shum |
| signal.jpg, signalwin.jpg, signallose.jpg | /signal |
| freeze.jpg, freezewin.jpg, freezelose.jpg | /freeze |
| tunnel.jpg, tunnelwin.jpg, tunnellose.jpg | /tunnel |
| escape.jpg, escapewin.jpg, escapelose.jpg | /escape |
| code.jpg, codewin.jpg, codelose.jpg | /code |
| magnet.jpg, magnetwin.jpg, magnetlose.jpg | /magnet |
| candle.jpg, candlewin.jpg, candlelose.jpg | /candle |
| pulse.jpg, pulsewin.jpg, pulselose.jpg | /pulse |
| orbit.jpg, orbitwin.jpg, orbitlose.jpg | /orbit |
| wall.jpg, wallwin.jpg, walllose.jpg | /wall |
| watcher.jpg, watcherwin.jpg, watcherlose.jpg | /watcher |
| controlroom.jpg, controlroomwin.jpg, controlroomlose.jpg | /controlroom |
| firesector.jpg, firesectorwin.jpg, firesectorlose.jpg | /firesector |
| mutation.jpg, mutationwin.jpg, mutationlose.jpg | /mutation |
| satellite.jpg, satellitewin.jpg, satellitelose.jpg | /satellite |
| mine.jpg, minewin.jpg, minelose.jpg | /mine |
| clock.jpg, clockwin.jpg, clocklose.jpg | /clock |
| lab.jpg, labwin.jpg, lablose.jpg | /lab |
| bunker.jpg, bunkerwin.jpg, bunkerlose.jpg | /bunker |
| storm.jpg, stormwin.jpg, stormlose.jpg | /storm |
| navigator.jpg, navigatorwin.jpg, navigatorlose.jpg | /navigator |
| icepath.jpg, icepathwin.jpg, icepathlose.jpg | /icepath |
| coinstack.jpg, coinstackwin.jpg, coinstacklose.jpg | /coinstack |
| target.jpg, targetwin.jpg, targetlose.jpg | /target |
| fuse.jpg, fusewin.jpg, fuselose.jpg | /fuse |
| web.jpg, webwin.jpg, weblose.jpg | /web |
| logicgate.jpg, logicgatewin.jpg, logicgatelose.jpg | /logicgate |
| depth.jpg, depthwin.jpg, depthlose.jpg | /depth |
| field.jpg, fieldwin.jpg, fieldlose.jpg | /field |
| ritual.jpg, ritualwin.jpg, rituallose.jpg | /ritual |
| trace.jpg, tracewin.jpg, tracelose.jpg | /trace |
| **Экономика** | |
| bal.jpg, refill.jpg, norefill.jpg, zl.jpg | /balance, /refill, налог |
| **Магазин и Premium** | |
| prem.jpg, kupprem.jpg, market.jpg, tehmarket.jpg, inventory.jpg, zelia.jpg | |
| mishka.jpg, otvertka.jpg, kluch32.jpg, status.jpg, gift.jpg, durev.jpg | |
| **Прочее** | |
| accaunt.jpg, vzor.jpg, steal.jpg, dostavka.jpg, Ban.jpg, kachalk.jpg | |

### Аудио (папка `assets/audio/`)

| Файл | Где используется |
|------|------------------|
| cityboy.ogg, ignat.ogg, dostavka.mp3, audio_dexter.mp3 | медиа-команды (если роутер media подключён) |

---

## Список только для 40 игр (120 файлов)

Чтобы в 40 играх показывались картинки, в `assets/images/` должны быть:

```
reactor.jpg      reactorwin.jpg      reactorlose.jpg
vault.jpg        vaultwin.jpg        vaultlose.jpg
dicepath.jpg     dicepathwin.jpg     dicepathlose.jpg
overheat.jpg     overheatwin.jpg     overheatlose.jpg
mindlock.jpg     mindlockwin.jpg     mindlocklose.jpg
bombline.jpg     bomblinewin.jpg     bomblinelose.jpg
liftx.jpg        liftxwin.jpg        liftxlose.jpg
doza.jpg         dozawin.jpg         dozalose.jpg
shum.jpg         shumwin.jpg         shumlose.jpg
signal.jpg       signalwin.jpg       signallose.jpg
freeze.jpg       freezewin.jpg       freezelose.jpg
tunnel.jpg       tunnelwin.jpg       tunnellose.jpg
escape.jpg       escapewin.jpg       escapelose.jpg
code.jpg         codewin.jpg         codelose.jpg
magnet.jpg       magnetwin.jpg       magnetlose.jpg
candle.jpg       candlewin.jpg       candlelose.jpg
pulse.jpg        pulsewin.jpg        pulselose.jpg
orbit.jpg        orbitwin.jpg        orbitlose.jpg
wall.jpg         wallwin.jpg         walllose.jpg
watcher.jpg      watcherwin.jpg      watcherlose.jpg
controlroom.jpg  controlroomwin.jpg  controlroomlose.jpg
firesector.jpg   firesectorwin.jpg   firesectorlose.jpg
mutation.jpg     mutationwin.jpg     mutationlose.jpg
satellite.jpg    satellitewin.jpg    satellitelose.jpg
mine.jpg         minewin.jpg         minelose.jpg
clock.jpg        clockwin.jpg        clocklose.jpg
lab.jpg          labwin.jpg          lablose.jpg
bunker.jpg       bunkerwin.jpg       bunkerlose.jpg
storm.jpg        stormwin.jpg        stormlose.jpg
navigator.jpg    navigatorwin.jpg    navigatorlose.jpg
icepath.jpg      icepathwin.jpg      icepathlose.jpg
coinstack.jpg    coinstackwin.jpg    coinstacklose.jpg
target.jpg       targetwin.jpg       targetlose.jpg
fuse.jpg         fusewin.jpg         fuselose.jpg
web.jpg          webwin.jpg          weblose.jpg
logicgate.jpg    logicgatewin.jpg    logicgatelose.jpg
depth.jpg        depthwin.jpg        depthlose.jpg
field.jpg        fieldwin.jpg        fieldlose.jpg
ritual.jpg       ritualwin.jpg       rituallose.jpg
trace.jpg        tracewin.jpg        tracelose.jpg
```

Если какого-то файла нет — бот отправит только текст (игра работает).

---

## Какие ассеты ЕСТЬ сейчас

**В проекте в папке `assets/images/` файлов нет (0 файлов).**  
Папка либо пустая, либо ассеты лежат в другом месте. При первом запуске бот создаёт `assets/images/` и `assets/audio/` и в логах пишет, каких файлов не хватает (`config.validate_assets()`).

---

## 40 игр — играбельные

**Да.** Все 40 игр теперь **играбельные**:

- Команды: `/reactor 100`, `/vault 50`, `/dicepath 200`, … (любая из 40 с суммой ставки).
- Механика одна: старт с множителя x1.00, кнопки «Забрать» и «Ещё». Каждое «Ещё» — либо рост множителя, либо обвал (проигрыш ставки). Таймер 3 минуты, по истечении — авто-забрать по текущему множителю.
- Ставка: от 10 до 5000 коинов (в конфиге `RISK40_BET_MIN`, `RISK40_BET_MAX`).
- Ассеты для каждой игры: `<slug>.jpg`, `<slug>win.jpg`, `<slug>lose.jpg` (всего 120 файлов для 40 игр). Если файла нет — бот пишет только текст.

Плюс по-прежнему работают: `/helpgame reactor`, `/helpgame vault`, … — правила без формул.
