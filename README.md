# Yandex Calendar Tools

Модуль работает с Яндекс Календарем через CalDAV: читает события, создает встречи, подбирает слоты, проверяет переговорки и переносит одиночные встречи.

## Файлы

- `yandex_calendar.py` - основной CLI.
- `contacts.json` - локальные алиасы участников.
- `rooms.json` - переговорки и resource email.
- `calendar_settings.json` - приоритет переговорок, маленькие комнаты и слова для поиска room resources.
- `work_calendar.json` - рабочее время 10:00-19:00 UTC+3, выходные и исключения производственного календаря РФ.
- `.env` - можно хранить локально в этой папке, но по умолчанию используется общий `../.env`.

## Настройка

В `.env` нужны:

```env
YANDEX_CALDAV_URL=https://caldav.yandex.ru
YANDEX_LOGIN=
YANDEX_APP_PASSWORD=
YANDEX_TIMEZONE=Europe/Moscow
YANDEX_ORGANIZER_NAME=
```

Скопируйте локальные алиасы участников:

```powershell
Copy-Item .\calendar\contacts.example.json .\calendar\contacts.json
```

`contacts.json` не публикуется. В него можно добавлять короткие алиасы:

```json
{
  "alex": {
    "name": "Alex Example",
    "email": "alex@example.com"
  }
}
```

Переговорки лежат в `rooms.json`, а порядок выбора и маленькие комнаты - в `calendar_settings.json`.

Опционально для поиска контактов:

```env
YANDEX_CARDDAV_LOGIN=
YANDEX_CARDDAV_APP_PASSWORD=
YANDEX_360_OAUTH_TOKEN=
YANDEX_360_ORG_ID=
```

## Основные команды

Список календарей:

```powershell
python .\calendar\yandex_calendar.py calendars
```

События на день:

```powershell
python .\calendar\yandex_calendar.py day --date 2026-04-22 --calendar "Мои события"
```

Создать встречу с Telemost по умолчанию:

```powershell
python .\calendar\yandex_calendar.py create --title "Демо" --start 2026-04-22T14:00 --end 2026-04-22T15:00 --calendar "Мои события" --attendee "Alex Example"
```

Создать без переговорки:

```powershell
python .\calendar\yandex_calendar.py create --title "Демо" --start 2026-04-22T14:00 --end 2026-04-22T15:00 --calendar "Мои события" --no-room
```

Подобрать слот:

```powershell
python .\calendar\yandex_calendar.py suggest --duration 60 --attendee "Alex Example" --attendee "sam@example.com" --from-date 2026-04-22 --to-date 2026-04-24
```

Подобрать только слот с переговоркой:

```powershell
python .\calendar\yandex_calendar.py suggest --duration 60 --attendee "Alex Example" --date 2026-04-22 --require-room
```

Перенести одиночную встречу:

```powershell
python .\calendar\yandex_calendar.py update --query "Планирование" --date 2026-04-22 --calendar "Мои события" --new-start 2026-04-22T16:00 --duration 45
```

Проверить изменение без сохранения:

```powershell
python .\calendar\yandex_calendar.py update --query "Планирование" --date 2026-04-22 --calendar "Мои события" --new-start 2026-04-22T16:00 --duration 45 --dry-run
```

Удалить встречу:

```powershell
python .\calendar\yandex_calendar.py delete --title "Тестовая встреча" --date 2026-04-22 --calendar "Мои события"
```

## Правила

- Telemost при создании добавляется через `X-TELEMOST-REQUIRED:TRUE`.
- При обновлении существующей встречи Telemost-поля не пересоздаются и не удаляются.
- Повторяющиеся встречи пока не обновляются: команда `update` явно блокирует recurring-события.
- Переговорки выбираются по приоритету: `Белуга -> Бывший -> Царская -> Боярка -> Чекушка`.
- `Боярка` и `Чекушка` считаются маленькими комнатами на 1 человека: автосоздание не добавляет их без явного `--room`, а подбор помечает их как требующие подтверждения.
