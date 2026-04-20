# Security

Do not publish local credentials or personal data.

Files that must stay local:

- `.env`
- `singularity/*.session`
- `calendar/contacts.json`
- `__pycache__/`

Before pushing, run:

```powershell
Get-ChildItem -Force -Recurse | Select-String -Pattern "TELEGRAM_API_HASH|YANDEX_APP_PASSWORD|YANDEX_360_OAUTH_TOKEN"
```

If a secret was committed, rotate it in the provider UI before continuing.

