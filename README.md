# Bot de oposiciones de informática

Monitor en Python para localizar convocatorias públicas de informática
compatibles con FP de Grado Superior, conservar su estado en SQLite y avisar por
Telegram cuando aparece una oportunidad o cambia un dato relevante.

La versión actual es `0.1.0`. Es una v1 funcional del núcleo y de varios
adaptadores oficiales, no una promesa de cobertura exhaustiva. Las bases de cada
convocatoria siguen siendo la referencia legal.

La especificación contractual y sus límites están en
[`ESPECIFICACION_REVISADA.md`](ESPECIFICACION_REVISADA.md).

## Qué hace hoy

- Consulta BOE, BOJA, BOP de Jaén y el tablón de Diputación mediante adaptadores
  específicos.
- Consulta el tablón dinámico del Ayuntamiento de Jaén con un adaptador propio e
  inspecciona IAAP y otras páginas locales mediante un adaptador HTML prudente.
- Extrae texto de PDF con PyMuPDF cuando el PDF tiene capa de texto.
- Clasifica relevancia, acceso, ámbito y titulación como `COMPATIBLE`,
  `NO_COMPATIBLE` o `REVISAR`.
- Conserva expedientes, referencias, URLs, hashes y ejecuciones en SQLite.
- Deduplica por identificador de origen, referencia oficial, clave canónica y
  coincidencia conservadora de título/organismo.
- Crea avisos de nueva convocatoria, revisión, actualización y recordatorio.
- Detecta como cambios materiales la fecha, hora y lugar de examen.
- Usa una outbox persistente para no reenviar automáticamente entregas ambiguas.
- Puede ejecutarse sin Telegram mediante `--dry-run` o `--collect-only`.
- Incluye tests unitarios del núcleo y un workflow de GitHub Actions.

## Requisitos

- Python 3.12 o posterior.
- Acceso HTTPS a las fuentes configuradas.
- Telegram solo si se quieren enviar avisos reales.
- Git y un repositorio GitHub solo si se usará la automatización incluida.

Ejecuta los comandos desde la raíz del proyecto, donde están `main.py` y
`config.yaml`.

## Instalación en Windows

En PowerShell:

```powershell
cd "C:\ruta\al\bot oposiciones"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Para desarrollar o ejecutar los tests:

```powershell
python -m pip install -r requirements-dev.txt
```

Si la política de PowerShell impide activar el entorno, se puede invocar el
intérprete directamente sin cambiar la política del sistema:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe main.py run --dry-run
```

## Instalación en Linux

```bash
cd "/ruta/al/bot oposiciones"
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Para desarrollo y tests:

```bash
python -m pip install -r requirements-dev.txt
```

La instalación editable crea también el comando `oposiciones-bot`. Todos los
ejemplos con `python main.py` tienen este equivalente:

```bash
oposiciones-bot run --dry-run
```

## Configuración

La configuración activa está en [`config.yaml`](config.yaml). La ruta de la base
se interpreta respecto al directorio del propio fichero de configuración, no
respecto al directorio desde el que se lance Python.

Opciones especialmente relevantes:

```yaml
collection:
  lookback_days: 10
  bootstrap_notify_days: 14
  fetch_document_text: true
  max_document_bytes: 15000000
  request_timeout_seconds: 25

database:
  path: data/oposiciones.db

reminders:
  enabled: true
  days_before: [7, 3, 1]

sources:
  boe:
    enabled: true
    kind: boe_api
```

El cargador actual comprueba que el YAML exista y que su raíz sea un objeto, pero
no aplica todavía un esquema completo de validación. Un `kind` desconocido falla
de forma explícita al construir los adaptadores.

### Variables de entorno

La aplicación lee directamente:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

El fichero `.env.example` es solo una referencia. La aplicación **no carga un
`.env` automáticamente**; hay que exportar las variables en el proceso, usar un
gestor de secretos o configurarlas como secretos de GitHub Actions.

## Configurar Telegram

1. Abre una conversación con `@BotFather` en Telegram.
2. Ejecuta `/newbot`, sigue los pasos y guarda el token.
3. Abre el chat con el bot recién creado y envíale un mensaje. Para un grupo,
   añade primero el bot y escribe un mensaje en el grupo.
4. Consulta `getUpdates` y toma `message.chat.id`. Los identificadores de grupos
   suelen ser negativos.

En PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN = "123456:token"
$updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/getUpdates"
$updates.result.message.chat.id
$env:TELEGRAM_CHAT_ID = "123456789"
```

En Linux:

```bash
export TELEGRAM_BOT_TOKEN='123456:token'
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
export TELEGRAM_CHAT_ID='123456789'
```

No copies el token a `config.yaml`, al historial de Git ni a una incidencia. La
URL de Bot API contiene el token, así que tampoco debe publicarse una captura de
ese comando.

Para una comprobación directa opcional en PowerShell:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/sendMessage" `
  -Body @{ chat_id = $env:TELEGRAM_CHAT_ID; text = "Prueba del bot de oposiciones" }
```

## Uso de la CLI

`python main.py` sin argumentos equivale a `python main.py run`: consulta las
fuentes, actualiza SQLite, genera recordatorios y trata de enviar hasta 20 avisos.
Si faltan las variables de Telegram, la recogida sí se guarda y los eventos
quedan `PENDING`.

### Simular sin cambiar disco ni Telegram

```bash
python main.py run --dry-run
```

El dry-run copia la base existente a memoria —o crea una base solo en memoria si
no existe—, consulta las fuentes y muestra los avisos que se enviarían. No cambia
`data/oposiciones.db` y no llama a Telegram. Si la base ya tenía eventos
pendientes, también pueden aparecer en la simulación.

Limitar la salida o elegir fuentes:

```bash
python main.py run --dry-run --limit 5 --source boe --source boja --source bop_jaen
```

`--source` se puede repetir y solo selecciona fuentes que además estén
`enabled: true` en `config.yaml`.

### Recoger sin enviar

```bash
python main.py run --collect-only
```

Este comando sí modifica SQLite: guarda expedientes, cambios, recordatorios y
eventos pendientes, pero no reclama ni envía la outbox. No necesita secretos de
Telegram.

### Enviar pendientes sin consultar fuentes

```bash
python main.py run --dispatch-only --limit 20
```

Requiere las dos variables de Telegram. Reclama eventos `PENDING` o `RETRYABLE`
y los envía. Devuelve código `3` si alguna entrega queda `UNCERTAIN` y código `2`
si faltan las credenciales.

### Ejecutar el ciclo completo

```bash
python main.py run --limit 20
```

Opciones útiles:

```text
--config RUTA       usa otro YAML
--source NOMBRE     restringe la recogida; se puede repetir
--today AAAA-MM-DD  sustituye la fecha actual para pruebas
--limit N           máximo de eventos reclamados para enviar
--log-level NIVEL   DEBUG, INFO, WARNING...
```

`--today` afecta a ventanas de consulta y recordatorios. Debe combinarse con
`--dry-run` salvo que se quiera modificar deliberadamente la base real con una
fecha simulada.

## Operar la outbox

### Inspeccionar

```bash
python main.py outbox list
python main.py outbox list --status PENDING
python main.py outbox list --status RETRYABLE --status UNCERTAIN
```

Estados actuales:

| Estado | Significado |
|---|---|
| `PENDING` | Persistido y todavía no reclamado. |
| `SENDING` | Reclamado antes del acceso a Telegram. |
| `SENT` | Telegram confirmó la entrega; es terminal. |
| `RETRYABLE` | Telegram rechazó o falló de forma concluyente; puede reclamarse de nuevo. |
| `UNCERTAIN` | La conexión terminó sin saber si Telegram aceptó el mensaje; no se reintenta automáticamente. |
| `SUPPRESSED` | Suprimido manualmente; no se reclama. |

### Reclamar y enviar en dos pasos

Es el flujo que usa GitHub Actions:

```bash
python main.py outbox claim --limit 20 --batch-file data/outbox_batch.json
python main.py outbox dispatch --batch-file data/outbox_batch.json
```

`claim` cambia inmediatamente los eventos a `SENDING`. No debe usarse solo para
«mirar» la cola. Si un proceso queda en `SENDING` más de 30 minutos, el siguiente
comando `run` lo recupera como `UNCERTAIN`; no lo reenvía por su cuenta.

### Resolver `UNCERTAIN`

Telegram no admite una clave de idempotencia elegida por el cliente. Si la
conexión cae después de aceptar el mensaje pero antes de responder, no se puede
saber automáticamente si se entregó. La v1 prioriza evitar duplicados:

1. Lista los inciertos:

   ```bash
   python main.py outbox list --status UNCERTAIN
   ```

2. Busca en el chat de Telegram la referencia corta que aparece al final de los
   mensajes, por ejemplo `Ref. aviso: 1234abcd5678`. Es el prefijo del
   `event_id` mostrado por `outbox list`.
3. Si el mensaje ya está en Telegram, suprímelo localmente:

   ```bash
   python main.py outbox suppress EVENT_ID_COMPLETO
   ```

4. Si has comprobado que no llegó, vuelve a ponerlo en cola y envíalo:

   ```bash
   python main.py outbox retry EVENT_ID_COMPLETO
   python main.py run --dispatch-only
   ```

Reintentar un `UNCERTAIN` es una decisión manual: puede duplicar un mensaje que
sí se entregó pero no se encontró al revisar el chat. `retry` también permite
reactivar un evento `SUPPRESSED` o `RETRYABLE`.

Esta política es **at-most-once best effort**: reduce duplicados automáticos a
cambio de que una entrega ambigua pueda requerir intervención o perderse. No es
una garantía de «exactamente una vez».

## Diagnóstico de SQLite

```bash
python main.py db check
python main.py db stats
```

`db check` ejecuta la comprobación de integridad de SQLite y debe imprimir `ok`.
`db stats` muestra cantidad de expedientes y eventos por estado.

No borres `data/oposiciones.db` para «limpiar» un error sin guardar una copia:
se perderían las identidades, el historial de avisos y la protección frente a
duplicados. Tampoco edites la base mientras el bot está en ejecución.

## Política de primera ejecución

La base está en modo arranque cuando todavía no contiene expedientes. En ese
primer recorrido:

- consulta la ventana `collection.lookback_days` —10 días por defecto—;
- no crea aviso para una fecha límite ya vencida;
- si hay fecha de publicación válida, solo crea aviso si cae dentro de
  `bootstrap_notify_days` —14 días por defecto—;
- si no hay fecha de publicación, solo avisa cuando existe un plazo o se ha
  reconocido un proceso especial;
- guarda silenciosamente los candidatos incluidos que no cumplen la regla de
  aviso, para deduplicarlos en el futuro.

Con los valores actuales, la ventana efectiva de fuentes fechadas normalmente
es de 10 días. Una convocatoria más antigua que siga abierta puede quedar
sembrada sin aviso si trae fecha de publicación; esta es una limitación de la
implementación actual.

`--dry-run` no consume el primer arranque porque nunca modifica la base en disco.

## Cobertura real de fuentes

| Fuente | Estado predeterminado | Adaptador actual | Cobertura y límites |
|---|:---:|---|---|
| BOE | Activa | API específica | Recorre 2A/2B y amplía tanto títulos TI como anuncios de localidades objetivo aunque el sumario sea genérico. Puede perder títulos atípicos fuera de esas anclas. |
| BOJA | Activa | API específica | Consulta por intervalo de fechas, pagina resultados y deduplica los identificadores; la ventana funciona también al cruzar el cambio de año. |
| BOP de Jaén | Activa | HTML/PDF específico | Recorre boletines diarios y extrae edictos candidatos. Descarga PDF y extrae texto; no hace OCR. |
| Diputación de Jaén | Activa | API específica | Consulta el tablón y su detalle; enlaza adjuntos, pero no extrae automáticamente el texto de cada adjunto. |
| IAAP | Activa | HTML genérico | Solo examina enlaces de las páginas configuradas. No es un rastreador dedicado del ciclo completo del proceso. |
| Ayuntamiento de Martos | Activa | HTML genérico | Busca señales en texto de enlace y su contenedor inmediato; amplía fichas HTML del mismo dominio y PDF enlazados directamente. Es sensible a cambios de plantilla. |
| Ayuntamiento de Torredelcampo | Activa | HTML genérico | Misma limitación del adaptador genérico; sensible a cambios de plantilla. |
| Ayuntamiento de Jaén | Activa | Adaptador específico | Descubre la pestaña pública de procesos selectivos y lee el dataset AJAX de la propia sede. No elude autenticación ni CAPTCHA. |
| Universidad de Jaén | Activa | HTML genérico | Vigila la página configurada, abre fichas del mismo dominio y conserva PDF enlazados; no pagina de forma específica. |
| Ayuntamiento de Torredonjimeno | Desactivada/degradada | HTML genérico sobre `info.0` | El tablón `/board` no se consulta por `robots.txt`. Además, la cadena TLS de `info.0` falla actualmente con la validación estándar; no se desactiva TLS. BOP/BOE son respaldo parcial, no equivalencia. |
| Punto de Acceso General | Desactivada | Adaptador PAG | Existe adaptador paginado, pero está desactivado: sus reglas de rastreo requieren ventana horaria y una espera de 60 s. El cliente actual no fuerza ese ritmo, por lo que no debe activarse en el cron sin implementar antes el cumplimiento. Además, PAG no cubre todo el empleo municipal. |

Los conectores HTML genéricos son deliberadamente prudentes: solo convierten un
enlace en candidato cuando el texto del enlace o su contenedor inmediato combina
una señal informática con otra de proceso selectivo. Pueden abrir fichas HTML del
mismo dominio, pero no paginan de manera específica, no ejecutan JavaScript y no
interpretan CAPTCHA.

Una fuente puede responder HTTP 200 y aun así no producir candidatos porque su
plantilla cambió. La v1 registra errores de descarga en `source_runs`, pero no
tiene todavía fixtures para todas las fuentes, detector de «silencio anómalo» ni
alertas de salud por Telegram. Las claves de umbral/enfriamiento presentes en
`config.yaml` quedan reservadas para esa mejora.

### PDFs

- Se admiten respuestas de hasta 15 MB por defecto.
- Se extraen como máximo 120 páginas.
- Se verifica la cabecera `%PDF`.
- No existe OCR; un PDF escaneado puede acabar como `REVISAR` o no aportar los
  campos esperados.
- BOE amplía HTML; BOP Jaén sí descarga sus edictos PDF; el adaptador HTML
  genérico extrae un PDF directo o amplía una ficha HTML y conserva sus enlaces.

## GitHub Actions

El workflow [`.github/workflows/oposiciones.yml`](.github/workflows/oposiciones.yml)
se ejecuta a las `06:17`, `10:17`, `14:17` y `18:17` UTC, además de admitir
`workflow_dispatch`. GitHub puede retrasar los cron y las horas locales cambian
con el horario de verano.

### Comandos desde Telegram

GitHub Actions no puede recibir mensajes de Telegram directamente. Para tener
comandos inmediatos sin mantener un servidor, el directorio
[`cloudflare-worker/`](cloudflare-worker/) incluye un puente para Cloudflare
Workers. Mantiene la búsqueda y SQLite en GitHub, y solo recibe comandos:

- `/buscar`: lanza una ejecución real de `oposiciones.yml`;
- `/estado`: muestra el estado de la última ejecución;
- `/ayuda` y `/start`: muestran una ayuda clara.

El Worker compara cada mensaje con `TELEGRAM_CHAT_ID`, valida el secreto del
webhook de Telegram y usa un token de GitHub de granularidad fina limitado a
este repositorio con permiso `Actions: Read and write`. Los secretos no deben
añadirse a `wrangler.toml`; se configuran cifrados en Cloudflare. Consulta
[`cloudflare-worker/README.md`](cloudflare-worker/README.md) para el esquema de
despliegue.

### Preparar el repositorio

Este directorio debe estar dentro de un repositorio GitHub. Si todavía no lo
está, crea el repositorio, añade el remoto y sube todos los archivos, incluida la
base inicial:

```bash
git init
git add .
git commit -m "Initial oposiciones bot"
git branch -M main
git remote add origin URL_DEL_REPOSITORIO
git push -u origin main
```

`data/oposiciones.db` se versiona deliberadamente porque los runners de GitHub
son efímeros. Los ficheros temporales `-journal`, `-wal`, `-shm` y
`data/outbox_batch.json` sí están ignorados.

En GitHub:

1. Abre **Settings → Secrets and variables → Actions**.
2. Crea `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` como *Repository secrets*.
3. Permite al workflow escribir contenido. El YAML solicita
   `permissions: contents: write`, pero la política del repositorio y la
   protección de la rama también deben permitir el `git push` del bot.
4. En **Actions → Buscar oposiciones → Run workflow**, ejecuta primero con
   `dry_run: true`.
5. Revisa el log y, después, ejecuta una vez con `dry_run: false`.

El workflow real realiza:

1. checkout e instalación;
2. suite de tests;
3. recogida y commit del SQLite con eventos `PENDING`;
4. reclamación de la outbox y segundo commit con eventos `SENDING`;
5. envío a Telegram;
6. commit final de recibos `SENT`, `RETRYABLE` o `UNCERTAIN`, incluso si el paso
   de envío ya iniciado falla. Si el fallo ocurre antes de llamar a Telegram,
   las reclamaciones vuelven a `PENDING` o no se publican en el remoto.

El grupo `concurrency: oposiciones-production-state` evita dos ejecuciones de ese
workflow a la vez. La persistencia previa al envío reintenta el `push` y verifica
si el commit llegó al remoto, pero no hace `rebase`: una edición/push manual
simultáneo sigue requiriendo intervención.

El workflow versiona expresamente `data/oposiciones.db`. Si se cambia
`database.path`, también hay que actualizar las tres rutas `git add` del YAML.

Si Telegram confirma el envío pero falla el último push, el repositorio remoto
conserva `SENDING`. En la siguiente ejecución pasará a `UNCERTAIN` y no se
reenviará automáticamente; aplica el runbook de la sección anterior. Si la rama
protegida impide commits directos, el workflow no puede persistir estado y no se
debe considerar operativo hasta corregir esa política o migrar la base.

SQLite es práctico para esta v1 de un solo escritor, pero cada modificación es
un cambio binario en el historial. Una base externa es el siguiente paso si
aumentan tamaño, usuarios o frecuencia.

## Tests

Ejecutar la suite local:

```bash
python -m pytest
```

Con cobertura:

```bash
python -m pytest --cov=oposiciones_bot --cov-report=term-missing
```

Los tests actuales cubren:

- clasificación de titulación, acceso y relevancia;
- excepciones geográficas de TAI y Junta;
- hashes, claves canónicas y snapshot semántico;
- deduplicación y cambios relevantes;
- separación de convocatorias homónimas por año/identificador y enlace prudente
  de publicaciones mediante referencias explícitas;
- atomicidad de proceso y evento;
- copia en memoria del dry-run;
- estados de outbox y recordatorios;
- renderizado Telegram, límites HTML y resultados de entrega simulados;
- fechas de plazo/examen y correcciones que vuelven incompatible un proceso;
- filtros comunes y parsers offline de BOE, BOJA, Diputación y Ayuntamiento de
  Jaén, incluidos paginación, deduplicación y respuestas AJAX.

La suite actual no hace peticiones reales y aún no contiene fixtures de cada
fuente local. Que los tests pasen no demuestra que una web externa no haya cambiado.
Una prueba de humo manual y no persistente de las fuentes principales es:

```bash
python main.py run --dry-run --source boe --source boja --source bop_jaen
```

## Añadir ubicaciones

Edita las listas de `config.yaml`:

```yaml
locations:
  priority:
    - Martos
    - Nuevo municipio
  nearby:
    - Otra localidad
```

La v1 hace coincidencia textual normalizada contra esas listas y también admite
la señal general `Jaén`. Aunque existe `radius_from_martos_km`, el código actual
**no calcula distancias ni descubre municipios automáticamente**. Añadir una
localidad solo tiene efecto cuando su nombre aparece en el candidato extraído.

TAI Estado y la informática de la Junta de Andalucía no se filtran por localidad.

## Añadir fuentes

### Fuente local sencilla

Para una página pública de Jaén cuyos enlaces ya describan las convocatorias se
puede reutilizar el adaptador genérico:

```yaml
sources:
  ayuntamiento_ejemplo:
    enabled: true
    kind: html
    urls:
      - "https://ejemplo.es/empleo-publico"
```

Después se puede ejecutar solo esa fuente:

```bash
python main.py run --dry-run --source ayuntamiento_ejemplo --log-level DEBUG
```

Limitaciones importantes:

- `GenericHTMLSource` busca señales solo en enlaces y padres inmediatos;
- amplía PDF directos y páginas HTML del mismo dominio, pero solo enlaza los PDF
  descubiertos dentro de estas últimas;
- asigna provincia Jaén a las fuentes genéricas salvo `iaap`, por lo que no se
  debe reutilizar sin cambios para otra provincia;
- no implementa paginación, navegador ni selectores propios de la web.

### Adaptador específico

Cuando la fuente tiene API, paginación, fichas de detalle o un HTML particular:

1. Crea una subclase de `SourceAdapter` en `oposiciones_bot/sources/`.
2. Implementa `fetch(FetchContext) -> list[Candidate]`.
3. Usa `HttpClient`; no hagas llamadas sin sus límites y reintentos.
4. Conserva `source_id`, URL, referencia y enlaces oficiales estables.
5. Pasa cada candidato por `enrich_candidate` si necesita extracción común.
6. Registra el nuevo `kind` en `oposiciones_bot/sources/registry.py`.
7. Añade tests con respuestas guardadas antes de activarlo por defecto.
8. Revisa `robots.txt` y condiciones del sitio.

No uses únicamente la URL como identidad si la fuente publica una referencia
oficial más estable.

## Estado honesto de la v1

Se considera implementado actualmente:

- núcleo SQLite transaccional;
- deduplicación estable y detección de cambios de campos conocidos;
- filtros conservadores con `REVISAR`;
- outbox con estados persistentes y política `UNCERTAIN`;
- mensajes Telegram y recordatorios de fechas explícitas;
- adaptadores específicos BOE, BOJA, BOP Jaén, Diputación y Ayuntamiento de Jaén;
- conectores HTML genéricos para IAAP y fuentes locales configuradas;
- automatización de un único escritor mediante GitHub Actions;
- tests unitarios del núcleo.

No está implementado todavía:

- OCR;
- cálculo de días hábiles o equivalencias jurídicas;
- radio geográfico real;
- navegador para páginas con JavaScript;
- parser específico y fixtures para cada ayuntamiento restante, IAAP y UJA;
- monitor de cambios de plantilla o silencio anómalo;
- alertas Telegram de salud de fuentes;
- revisión interactiva de posibles fusiones;
- base externa o varios escritores;
- entrega exactamente una vez;
- garantía de cobertura total.

La prioridad de trabajo futura es completar fixtures y pruebas de contrato por
fuente, convertir los conectores locales críticos restantes en adaptadores
específicos, medir silencios anómalos y migrar el estado fuera de Git cuando el
volumen lo justifique.

## Seguridad y operación

- No se versionan tokens ni chat IDs.
- No se deben registrar URLs completas de Bot API que contengan el token.
- La base guarda información pública y estado operativo, pero conviene mantener
  el repositorio privado si no se quiere exponer el historial personal de avisos.
- El bot no elude autenticación, CAPTCHA ni restricciones de rastreo.
- Un resultado `REVISAR` exige abrir el enlace y comprobar las bases oficiales.
- Antes de actualizar dependencias o parsers, conserva una copia de
  `data/oposiciones.db` y ejecuta `python main.py db check`.
# Perfil personal de avisos

El perfil configurado ahora exige puestos informáticos (técnico, auxiliar,
especialista y equivalentes), grupo B o C1 confirmado y una titulación admitida
de DAM, DAW, ASIR o la familia profesional de Informática y Comunicaciones.
No basta con que el anuncio mencione herramientas informáticas o Bachiller.
Las bases siguen siendo la autoridad: pertenecer a una familia no garantiza
que cualquier título concreto sea admitido.

Las nuevas oportunidades requieren acceso libre/mixto y fecha límite oficial
confirmada, no vencida. Anuncios de notas, exámenes o admitidos no son nuevas
oportunidades. Las actualizaciones y recordatorios requieren seguimiento
explícito; el bot no sabe si te has inscrito realmente.
Los avisos pendientes antiguos se revisan otra vez antes de enviarlos.

SAS: se consulta el tablón de ofertas específicas y la página de evolución de
Técnico Especialista en Informática de OEP 2025, además del BOJA. No es una
garantía de cobertura exhaustiva de todos los centros o páginas paginadas.
Las convocatorias informáticas con datos inciertos se conservan en el catálogo
como POR REVISAR, no como inscripción confirmada. Grupos conocidos distintos
de B/C1, exigencia universitaria y puestos no informáticos siguen excluidos.

## Consulta y seguimiento desde Telegram

Las búsquedas automáticas se programan todos los días a las **08:30, 12:30,
17:30 y 20:30** en `Europe/Madrid` (España peninsular), tanto en verano como
en invierno. GitHub Actions puede iniciar una ejecución con retraso.

La búsqueda es incremental por fuente: desde la fecha de su última consulta
correcta hasta hoy, incluyendo ambos días. Una consulta fallida no avanza el
punto de control. La primera consulta usa los 10 días configurados. Las
consultas manuales y programadas comparten esos puntos de control. Los
tablones sin archivo histórico solo permiten revisar lo que aún publican;
una ventana de fechas no garantiza recuperar documentos retirados.
Los registros seguidos en los tablones de Jaén se revisan aunque su fecha
original sea anterior a la ventana. Los avisos repetidos siguen deduplicados.

- `/buscar`: actualizar fuentes en GitHub; al acabar usa `/convocatorias`.
- `/convocatorias N`: lista paginada de oportunidades, revisión y seguimiento.
- `/detalle ID` o `/buscar ID`: ficha disponible, enlaces e historial detectado.
- `/seguir ID`: guardar seguimiento explícito; GitHub confirma al terminar.
- `/dejar ID`: quitar seguimiento; GitHub confirma al terminar.
- `/seguimientos N`: lista de seguimientos ya guardados.
- `/ayuda`: instrucciones y actualización del menú de comandos.

El ID es el prefijo de 8 caracteres que muestra el catálogo. Seguir no equivale
a inscribirse. Las fichas muestran datos extraídos, no sustituyen las bases.
Los comandos de seguimiento son asíncronos y comparten la misma exclusión
mutua que la búsqueda. Comprueba el enlace de ejecución si no llega confirmación.

Los documentos de Martos se agrupan solo cuando comparten una carpeta oficial
de convocatoria (`/download/ID/convocatoria.../`). No se agrupan por semejanza
del título. Los registros antiguos se conservan y sus IDs redirigen a la ficha
agrupada. Cada documento conserva su enlace; los formularios no sustituyen las
bases como evidencia de titulación. «Proceso avanzado o cerrado» describe la
fase, mientras «Seguimiento ACTIVO/NO ACTIVO» refleja tu elección explícita.

`data/catalog.json` es una exportación sin credenciales, con fechas de consulta
y cobertura de fuentes, regenerada al recoger datos y guardar seguimientos.
El Worker la lee del repositorio público sin ampliar permisos del token.
La base y el catálogo del repositorio público incluyen los procesos seguidos:
no guardes información personal de inscripción. No se añade almacenamiento
ni servicios de pago en Cloudflare.
