# Yandex Calendar Tools

Инструмент помогает быстро планировать рабочие встречи в Яндекс Календаре без ручной проверки нескольких календарей и переговорок. Он работает через CalDAV: читает занятость, подбирает свободные слоты, создает встречи с участниками, добавляет Яндекс Телемост и помогает переносить уже созданные события.

## Что умеет

- Подбирает время для встречи с учетом занятости организатора и участников.
- Учитывает рабочее окно, выходные и исключения производственного календаря.
- Проверяет доступность переговорок и выбирает комнату по заданному приоритету.
- Отдельно помечает маленькие комнаты, которые не стоит добавлять без подтверждения.
- Создает встречи в Яндекс Календаре с участниками и нативной ссылкой Яндекс Телемост.
- Переносит и обновляет одиночные встречи, не пересоздавая Телемост.
- Поддерживает локальные алиасы участников: можно писать короткие имена вместо email.

## Типовые сценарии

- "Найди час на этой неделе для встречи с Аней и Сергеем".
- "Создай встречу завтра в 15:00 с продуктовой командой".
- "Подбери слот только со свободной переговоркой".
- "Перенеси встречу на 30 минут позже".
- "Добавь участника во встречу, Телемост не трогай".

## Что нужно сделать в Яндексе

Для базового сценария, то есть просмотра календаря, создания встреч, переноса встреч, проверки занятости и переговорок, OAuth-приложение создавать не нужно. Нужен пароль приложения для CalDAV-доступа к календарю.

### 1. Создать пароль приложения для календаря

1. Войдите в нужный Яндекс-аккаунт.
2. Откройте раздел безопасности Яндекс ID: [id.yandex.ru/security](https://id.yandex.ru/security).
3. Перейдите в `Доступ к вашим данным` -> `Пароли приложений`.
4. Можно открыть справку с точными шагами: [Пароли приложений | Яндекс ID](https://yandex.ru/support/id/ru/authorization/app-passwords).
5. Нажмите `Создать пароль приложения`.
6. Выберите тип приложения `CalDAV-клиент для Яндекс Календаря`.
7. Назовите пароль, например `yandex-meeting-scheduler calendar`.
8. Скопируйте пароль сразу: Яндекс покажет его только один раз.
9. Укажите его в `.env` как `YANDEX_APP_PASSWORD`.

Пример обязательной части `.env`:

```env
YANDEX_CALDAV_URL=https://caldav.yandex.ru
YANDEX_LOGIN=your-login@example.com
YANDEX_APP_PASSWORD=your-calendar-app-password
YANDEX_TIMEZONE=Europe/Moscow
YANDEX_ORGANIZER_NAME=Your Name
```

По справке Яндекса пароль приложения может начать действовать не сразу, а через несколько часов. Если подключение сразу не проходит, проверьте логин/пароль и повторите позже.

### 2. Проверить CalDAV-доступ

После заполнения `.env` запустите:

```powershell
python .\calendar\yandex_calendar.py calendars
```

Если всё настроено корректно, команда покажет список календарей, например `Мои события`.

Официальная справка по CalDAV:

- [Синхронизация Яндекс Календаря с календарём на компьютере](https://yandex.com/support/yandex-360/business/calendar/en/sync/sync-desktop)
- [Синхронизация Яндекс Календаря с мобильным календарём](https://yandex.com/support/yandex-360/customers/calendar/web/en/sync/sync-mobile)

### 3. Опционально: создать пароль приложения для контактов

Это нужно только если вы хотите, чтобы инструмент пытался искать участников через CardDAV-контакты, когда их нет в `contacts.json`.

1. Снова откройте [id.yandex.ru/security](https://id.yandex.ru/security).
2. Перейдите в `Доступ к вашим данным` -> `Пароли приложений`.
3. Создайте новый пароль приложения.
4. Выберите тип `CardDAV-клиент для синхронизации контактов`.
5. Назовите пароль, например `yandex-meeting-scheduler contacts`.
6. Скопируйте пароль и добавьте в `.env`:

```env
YANDEX_CARDDAV_LOGIN=your-login@example.com
YANDEX_CARDDAV_APP_PASSWORD=your-contacts-app-password
```

### 4. Опционально: создать OAuth-приложение для Yandex 360 Directory

Это нужно только если в вашей организации доступен Yandex 360 Directory API и вы хотите искать сотрудников в корпоративной директории, когда участника нет ни в `contacts.json`, ни в CardDAV.

1. Откройте консоль Yandex OAuth: [oauth.yandex.com](https://oauth.yandex.com/).
2. Создайте приложение по ссылке: [oauth.yandex.com/client/new/](https://oauth.yandex.com/client/new/).
3. Название приложения: например `yandex-meeting-scheduler`.
4. В правах доступа выберите права для чтения организации и пользователей Yandex 360 Directory.
5. Если интерфейс показывает technical scopes, нужны:
   - `directory:read_organization`
   - `directory:read_users`
6. На шаге `Platforms` добавьте redirect URI для ручного получения токена:

```text
https://oauth.yandex.ru/verification_code
```

7. Сохраните приложение и скопируйте `Client ID`.
8. Получите токен вручную, открыв ссылку, где `<CLIENT_ID>` заменен на Client ID вашего приложения:

```text
https://oauth.yandex.ru/authorize?response_type=token&client_id=<CLIENT_ID>
```

9. Нажмите `Allow` / `Разрешить`.
10. Скопируйте `access_token` из открывшейся страницы или URL после `#access_token=`.
11. Добавьте его в `.env`:

```env
YANDEX_360_OAUTH_TOKEN=your-oauth-token
YANDEX_360_ORG_ID=
```

Если у токена видны несколько организаций, укажите конкретный `YANDEX_360_ORG_ID`.

Официальная справка по OAuth:

- [Регистрация приложения в Yandex OAuth](https://yandex.com/dev/id/doc/en/register-client)
- [Yandex OAuth control panel](https://yandex.com/dev/id/doc/en/oauth-cabinet)
- [Получение токена вручную / Debug token](https://yandex.com/dev/id/doc/en/tokens/debug-token)

## Файлы

- `yandex_calendar.py` - основной CLI.
- `contacts.json` - локальные алиасы участников.
- `rooms.json` - переговорки и resource email.
- `calendar_settings.json` - приоритет переговорок, маленькие комнаты и слова для поиска room resources.
- `work_calendar.json` - рабочее время 10:00-19:00 UTC+3, выходные и исключения производственного календаря РФ.
- `.env` - можно хранить локально в этой папке, но по умолчанию используется общий `../.env`.

## Настройка

Скопируйте пример конфига:

```powershell
Copy-Item .env.example .env
```

Заполните `.env`:

```env
YANDEX_CALDAV_URL=https://caldav.yandex.ru
YANDEX_LOGIN=your-login@example.com
YANDEX_APP_PASSWORD=your-calendar-app-password
YANDEX_TIMEZONE=Europe/Moscow
YANDEX_ORGANIZER_NAME=Your Name
```

`YANDEX_APP_PASSWORD` - это не обычный пароль от Яндекса, а пароль приложения для календаря.

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

## Ограничения

- Инструмент не хранит пароли и токены в коде. Все секреты должны лежать только в локальном `.env`.
- Для участников лучше заранее заполнить `contacts.json`, если корпоративная адресная книга недоступна через CardDAV.
- Обновление recurring-встреч пока заблокировано специально, чтобы случайно не изменить всю серию.
- Доступность участников и переговорок зависит от того, какие free-busy данные возвращает ваш Яндекс 360.
