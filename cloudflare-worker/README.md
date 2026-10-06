# Comandos de Telegram con Cloudflare Workers

Este Worker sirve de puente inmediato:

```text
Telegram -> Cloudflare Worker -> workflow_dispatch de GitHub -> búsqueda Python
```

Comandos autorizados únicamente para `TELEGRAM_CHAT_ID`:

- `/buscar`: inicia `oposiciones.yml` con `dry_run=false`.
- `/estado`: consulta la última ejecución.
- `/ayuda` y `/start`: explican el uso.

Variables no secretas ya incluidas en `wrangler.toml`:

- `GITHUB_OWNER`
- `GITHUB_REPO`
- `GITHUB_WORKFLOW`
- `GITHUB_REF`

Secretos que deben configurarse en Cloudflare, nunca en Git:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GITHUB_TOKEN`: token de acceso de granularidad fina limitado al repositorio,
  con permiso **Actions: Read and write**.
- `WEBHOOK_SECRET`: cadena aleatoria de letras, números, `_` y `-` utilizada
  para validar la cabecera enviada por Telegram.

El endpoint de salud es `/health` y el webhook es `/telegram`.

Después de publicar el Worker hay que registrar en Telegram la URL
`https://NOMBRE.workers.dev/telegram` mediante `setWebhook`, incluyendo el mismo
`WEBHOOK_SECRET` en el parámetro `secret_token`. Los comandos visibles se pueden
configurar sin API desde BotFather con `/setcommands`.
