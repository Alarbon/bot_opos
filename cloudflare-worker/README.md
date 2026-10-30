# Comandos de Telegram con Cloudflare Workers

Este Worker sirve de puente inmediato:

```text
Telegram -> Cloudflare Worker -> workflow_dispatch de GitHub -> búsqueda Python
```

Comandos autorizados únicamente para `TELEGRAM_CHAT_ID`:

- `/buscar`: **solicita** `oposiciones.yml` con `dry_run=false`. Antes consulta
  GitHub y, si ya hay una búsqueda en cola o en curso, no solicita otra. La
  respuesta nunca dice que la búsqueda haya terminado: el resultado llega en el
  resumen final del workflow.
- `/estado`: distingue última solicitada, iniciada, completada y correcta, si
  hay una en marcha y el resumen de la última completada (fuentes correctas,
  con aviso y fallidas; registros; nuevos; avisos enviados). Si GitHub no
  devuelve un dato, lo dice en lugar de inventarlo.
- `/ayuda` y `/start`: explican el uso.

Protecciones:

- Se ignoran los mensajes editados (editar un `/buscar` antiguo no lanza otra
  búsqueda) y los comandos con más de 10 minutos de antigüedad (reentregas de
  Telegram tras una caída).
- Los secretos se comparan en tiempo constante.
- Los errores de GitHub se traducen a un motivo comprensible; un fallo de red
  al solicitar se comunica como «no sé si llegó», nunca como éxito.

Límites conocidos: el Worker no guarda estado (sin KV), así que no deduplica
`update_id`; la protección frente a solicitudes repetidas es la consulta de
ejecuciones activas más el grupo de concurrencia del workflow.

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
- `BOP_PROXY_TOKEN` (recomendado): cadena aleatoria propia para el proxy del
  BOP. Debe tener el mismo valor como secreto de GitHub Actions
  (`BOP_PROXY_TOKEN`). Mientras no exista, el Worker y el workflow usan el
  token de Telegram como antes; separar ambos evita reutilizar la credencial
  del bot.

Tras desplegar esta versión hay que volver a llamar a `/admin/configure` para
que Telegram deje de enviar mensajes editados (`allowed_updates: ["message"]`).

El endpoint de salud es `/health` y el webhook es `/telegram`.

Después de publicar el Worker, una petición `POST` a `/admin/configure` con la
cabecera `Authorization: Bearer <WEBHOOK_SECRET>` registra automáticamente el
webhook y los comandos visibles en Telegram. La ruta está protegida por el mismo
secreto y no devuelve ninguna credencial.
