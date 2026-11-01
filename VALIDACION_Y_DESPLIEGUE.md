# Validación local de bot_opos — 1 de noviembre de 2026

Trabajo exclusivamente local. Sin commits, push, despliegues, cambios de secretos, workflows remotos ni mensajes reales de Telegram. La imagen no versionada preexistente se conserva. `data/oposiciones.db`, `data/catalog.json` y `data/latest_run.json` no se han modificado.

## Errores demostrados y correcciones

| Error | Causa | Corrección |
|---|---|---|
| BOJA HTTP 400 del run 41315381786 | Reproducido con fechas 2026-10-31 a 2026-11-01. Con inicio 2026-10-30 devuelve 86 documentos. El contrato OpenAPI permite los parámetros enviados; no se ha demostrado un cambio de esquema ni falta de autenticación. | Ante un 400 inicial, una única ampliación de siete días. Se valida y pagina el resultado completo y se filtra al intervalo original. Si falla de nuevo, ERROR y checkpoint intacto. |
| BOJA podía acreditar cobertura con JSON mal formado | `results` ausente se interpretaba como lista vacía; páginas truncadas podían terminar silenciosamente. | Validación de esquema, total, ID, fecha, contenido y progreso. Límite de páginas; error ante truncamiento. PDF también desde `publicUrl` plano. |
| B1.2000 tenía 2026 plazas y MIXTO | Extracción desde texto aplanado: fechas junto a «plazas» y un anuncio general que menciona libre y promoción interna. | Campos estructurados de plazas/acceso; plazo de solicitudes desde la fila correspondiente, tomando su fecha final. |
| B1.2000 tenía cierre 10/06/2026 | Se tomaba la primera fecha del periodo. | El cierre oficial de esa ficha es 23/06/2026. No se confunden fechas de actualización con publicación. |
| IAAP y BOP podían marcar OK aunque fallase un documento | Excepciones de descarga/extracción solo se escribían en log. | PARTIAL, documento pendiente y cobertura sin avanzar sobre el fallo. Se conservan candidatos recuperados y se reintenta en la siguiente búsqueda. |
| Enlaces genéricos heredaban toda la página como contexto | La alternativa final de `_result_container` devolvía `body`, pese a excluirlo antes. | Contexto vacío para padres globales; un enlace de navegación no hereda una convocatoria ajena. |
| Fechas provisionales perdían su marca en SQLite | `raw.exam_date_provisional` no se conservaba en la reconstrucción. | Marca en snapshot semántico, recuperación para catálogo y detección de cambios de provisionalidad. Sin migración destructiva. |
| `/estado` podía confundir GitHub correcto con cobertura correcta | Conclusión `success` del workflow. | Resultado de fuentes explícito: sin novedades, parcial o fallido. Solo usa el informe del run correspondiente. |

## Fichas oficiales contrastadas

- [B1.2000, P2B12000](https://portalempleopublico.juntadeandalucia.es/sede/acceso-tramites/seguimiento-procesos-selectivos/P2B12000): 20 plazas, promoción interna, grupo B, personal funcionario; solicitudes 10–23/06/2026. La página ya está en una fase de autobaremo. No acredita un destino concreto en Jaén ni la elegibilidad personal del usuario. Las bases enlazadas no se han validado íntegramente; requisitos específicos de promoción y titulación quedan pendientes.
- [C1.2003, ficha concreta almacenada](https://portalempleopublico.juntadeandalucia.es/sede/acceso-tramites/seguimiento-procesos-selectivos/c12003-ayudantes-tecnicos-opcion-informatica): 40 plazas, acceso libre, C1, personal funcionario. Convocatoria publicada en BOJA de 01/10/2024; está en cobertura de plazas adicionales, no en una nueva inscripción. El [anexo I.A de las bases](https://portalempleopublico.juntadeandalucia.es/sites/default/files/migracion_procesos/unique/Resolucion_DGRRHHYFP_Convocatoria_C1.2_AYTE._TECNIC.pdf) enumera Bachiller, Técnico y alternativas legales. No se ha certificado el cumplimiento de todos los requisitos personales ni destino Jaén. El importador mantiene REVISAR cuando no ha extraído titulación verificable.

No se deducen cifras de otras OEP o procedimientos que comparten código. La publicación administrativa y el estado actual se distinguen del periodo de inscripción.

## Telegram

- Menú de inicio, catálogo y seguimientos con cinco fichas por página; anterior, siguiente y regreso.
- Detalles y navegación editan el mismo mensaje; no ejecutan búsquedas.
- Detalle, seguir/dejar, fuente oficial y copia de `/detalle UUID_COMPLETO`.
- Callbacks con UUID completo estable; alias persistentes admitidos; nunca índices del catálogo como identidad.
- Autorización de chat y usuario antes de leer datos; secreto del webhook; `answerCallbackQuery` antes de consultar GitHub.
- Seguimiento por el workflow existente. Mensaje pendiente al solicitar; la confirmación existente llega tras guardar SQLite y catálogo.
- Bloqueo temporal de dobles pulsaciones y comprobación del workflow de seguimiento. Operaciones absolutas e idempotentes. No hay deduplicación global duradera entre isolates: sigue existiendo una ventana antes de que GitHub publique una nueva ejecución. No se promete entrega exactamente una vez.
- HTML escapado con presupuestos de longitud, fechas Europe/Madrid, botones también en notificaciones y resúmenes. Límites de callback y copia cubiertos por tests.
- En privado se autoriza al mismo ID del chat. En grupos se exige `TELEGRAM_USER_ID`. El Worker no accede directamente a SQLite.

Antes: `/detalle f65b4f61` y copia manual del identificador.
Después: botón **🔎 Ver detalles** → ficha → **⭐ Seguir** / **🔕 Dejar de seguir**, **🔗 Fuente oficial**, **📋 Copiar comando completo**, **↩️ Volver al listado**.

Antes: «Plazas: 2026 · Acceso: MIXTO».
Después, en la copia revisada: «Plazas: 20 · Acceso: PROMOCION_INTERNA_EXCLUSIVA · NO_COMPATIBLE». La ficha no se ofrece como inscripción abierta.

Antes: workflow correcto con fuentes parciales.
Después: «⚠️ Búsqueda completada con cobertura parcial». Los documentos procesados se indican como no medidos por separado; no se inventa ese contador.

## Validación y estado de fuentes

- Suite Python: **296 pruebas superadas**, cero fallos, 15,62 segundos; cinco advertencias de deprecación de SWIG/PyMuPDF. Comando: `python -m pytest -o addopts='' -q`.
- Worker: 52 pruebas superadas, cero fallos, con `node --test cloudflare-worker/src/index.test.mjs`.
- Tests de Python bloquean la red; Telegram está simulado. Cubren BOJA, extracción, clasificación, checkpoints, SQLite, outbox, duplicados y recordatorios, además de los nuevos casos de regresión.
- Verificación HTTP real de lectura: adaptador BOJA devuelve cero candidatos en 31/10–01/11 con recuperación validada; para 30/10 devuelve cuatro candidatos con ID, fecha y URL oficial. Esto valida extracción, no cuatro oportunidades compatibles.
- SQLite original: `PRAGMA integrity_check = ok`; hash SHA-256 idéntico antes y después de la reparación en copia.
- Informe remoto [run 41315381786](https://github.com/Alarbon/bot_opos/actions/runs/41315381786): BOJA ERROR 400, IAAP PARTIAL por ConnectTimeout, BOP PARTIAL con archivo hasta 30/10/2026; las otras siete fuentes figuraban OK. Es el informe observado, no una nueva búsqueda completa de producción.
- IAAP: dos fichas concretas accesibles y capturadas para pruebas; no se acredita cobertura completa de sus dos listados. BOP: no se acredita cobertura posterior al archivo observado. Los fallos de origen permanecen visibles.
- Contrato oficial consultado: [OpenAPI BOJA](https://datos.juntadeandalucia.es/api/v0/boja/openapi.json). GET `search_pagination`, `order_by=dateUTC`, `mode=DESC`, `size<=100`, `page` desde cero, fechas ISO y `campos` repetidos. Sin autenticación en ese contrato. [Bot API oficial](https://core.telegram.org/bots/api#inlinekeyboardbutton).

## Archivos cambiados

Producción: `cloudflare-worker/src/index.js`, `cloudflare-worker/README.md`; `oposiciones_bot/{catalog,classifiers,db,enrichment,matching,pipeline,telegram}.py`; `oposiciones_bot/sources/{boja,bop_jaen,generic_html,iaap_fields}.py`; `scripts/{run_report,repair_iaap_copy}.py`.

Pruebas: `cloudflare-worker/src/index.test.mjs`; `tests/test_{iaap_fields,personal_eligibility,pipeline_observability,run_report,source_boja,source_bop_jaen,source_resilience,telegram}.py`; `tests/fixtures/iaap_{b12000,c12003}.html`. Informe: este archivo.

## Pendientes y límites

Corregido y probado localmente: errores y botones descritos. Pendiente de producción: webhook con callbacks, permisos efectivos, envío real y confirmación del seguimiento. No verificable sin esa prueba: experiencia del cliente Telegram y entrega real de avisos.

No se ha completado una auditoría integral de todas las fuentes/anexos ni la recuperación de toda publicación tardía. No se ha cambiado el solapamiento incremental (sigue en cero), la programación ni los workflows. La opción de zona horaria de Actions no se ha investigado ni aplicado. Mantener estas mejoras secundarias pendientes evita modificar políticas de cobertura sin validación suficiente. Los tests existentes de recordatorios y outbox se reutilizan; no garantizan por sí solos producción.

Solo requieren intervención del propietario: publicar código, desplegar Worker, reconfigurar webhook, incorporar una reparación de la base remota actual y efectuar pruebas reales. No se solicita permiso en este chat porque el despliegue lo realizará el usuario.

## Preparación definitiva del despliegue — revisión posterior del 01/11/2026

Este procedimiento sustituye al orden de despliegue de la primera entrega. **Los comandos de publicación, pausa, configuración y prueba real siguientes son para el propietario: no se han ejecutado en esta sesión.** Se han realizado exclusivamente lecturas remotas y trabajo local. No se ha sustituido la SQLite real, ni cambiado secretos, ni enviado Telegram.

### Persistencia comprobada y ensayo sobre producción actual

La fuente de verdad es el blob **`main:data/oposiciones.db` del repositorio `Alarbon/bot_opos`**, no la base histórica del escritorio, un artifact de Actions, Cloudflare D1 o el disco temporal de un runner. `config.yaml` apunta a ese archivo. Cada workflow hace checkout de la rama actual después de esperar su turno. Ambos comparten `concurrency.group: oposiciones-production-state`, sin cancelar la ejecución activa. `scripts/publish_state.py` publica `data/oposiciones.db`, `data/catalog.json` y `data/latest_run.json`; rechaza conflictos de estado remoto y no fuerza el push. Una publicación manual no participa en ese bloqueo: requiere pausa y comprobación de SHA.

La búsqueda persiste recogida → reclama outbox y persiste `SENDING` → envía → persiste recibos → informe. Seguimiento modifica SQLite, exporta catálogo, publica y después confirma. Por ello **no cancelar una búsqueda durante envío o guardado**. Un runner fallido puede conservar recibos que no llegaron a Git; revisar sus pasos antes de restaurar o reintentar.

Se descargaron por HTTPS, fijados al commit **`9f53cba123ba5c4e72bd2d96a735f218075e693f`**, SQLite, catálogo, informe y los componentes relevantes. Los diez últimos runs observados estaban completados; esto es una observación, no una pausa de producción. SHA-256 del blob SQLite: `28681ada9ddbb1280a35aab104057cef14e679a7606fe69e26ddf0cbc56ff18c`.

Resultado del ensayo local sobre **esa copia remota**, respaldada con `sqlite3.Connection.backup`:

| Comprobación | Resultado |
|---|---|
| Integridad y relaciones | `integrity_check=ok`, `foreign_key_check=[]`, esquema 6 |
| Convocatorias / seguimiento / alias | 24 / 1 / 17, conservados |
| Outbox | 2 filas: una `SENT`, una `SUPPRESSED`; filas completas intactas |
| Otros datos | 24 source_items, 69 process_urls, 4 referencias, 268 source_runs, 10 checkpoints, 0 reminders; tablas completas idénticas |
| Reparación | Solo cambian campos previstos de B1.2000 y C1.2003, sin cambiar sus UUID ni crear eventos |
| Idempotencia | Segunda reparación: igualdad de todas las filas de todas las tablas, incluidas fechas |
| Catálogo | Versión 2; exportarlo no cambia ninguna fila en esta base; UUID únicos y alias válidos; seguimiento visible |
| Cambio visible | 4 → 3 fichas: B1.2000 queda excluida por promoción interna; sigue en SQLite |

Se corrigió un único problema operativo del reparador: repetía `last_changed` aunque no hubiera cambios. Ahora no actualiza una fila ya corregida. Prueba reforzada con un reloj futuro y comparación de todas las tablas. No hay cambio de esquema. Las reparaciones quedan documentadas en `.audit.json`; no se inventan notificaciones históricas.

Los ficheros del ensayo están fuera del repositorio, en `C:\Users\usuario\.codex\visualizations\2026\11\01\01a19904-af78-78c2-a619-4282122eb8a3\preparacion-despliegue`: `manifest.json`, `validacion.json`, `produccion/`, `backup.db`, `reparada.db`, `repetida.db`, `reparada.audit.json` y `catalog.json`. Son evidencia de esta revisión; **repetir la preparación con el último commit durante la pausa**. No publicar esas copias si producción ha avanzado.

### Compatibilidad y riesgos que determinan el orden

| Combinación | Evaluación |
|---|---|
| Worker nuevo + Python/catálogo anteriores | Compatible: lee `processes`, UUID, alias y `followed` existentes; el campo provisional es opcional. Los mismos inputs de `seguimiento.yml` siguen vigentes. |
| Python nuevo + SQLite actual | Esquema 6 en ambos; nuevo dato provisional dentro de JSON opcional. No hay ALTER obligatorio. Python anterior puede leer las filas reparadas. |
| Python nuevo notificando + Worker anterior | Evitar: genera teclados cuyos callbacks el Worker anterior no maneja. |
| Catálogo nuevo + Worker anterior | JSON sigue en versión 2, pero el usuario aún no dispone de la navegación nueva. |
| Worker nuevo + webhook solo `message` | Los botones aparecen pero Telegram no entrega callbacks; reconfigurar antes de probarlos. |
| Botón antiguo + ficha vigente/alias | Resuelve UUID completo contra catálogo actual. No depende del orden. |
| Botón antiguo + ficha retirada | Error seguro y enlace al listado. B1.2000 es un caso esperado; no implica borrado de datos. |

**Orden elegido:** pausar ambos workflows y drenar → respaldo y reparación en copia → Worker nuevo → publicar conjuntamente código Python, SQLite revisada y catálogo → configurar webhook → habilitar solo seguimiento y probarlo → habilitar búsqueda y ejecutar una sola búsqueda. Así no hay envíos nuevos con un Worker antiguo. No se han cambiado los YAML: los remotos coinciden con los locales salvo finales de línea. No hace falta una publicación separada de Actions.

Límites operativos que se aceptan para este despliegue controlado:

- SQLite y JSON se publican en el mismo commit, pero peticiones separadas a `raw.githubusercontent.com/main` pueden ver temporalmente revisiones distintas. Comprobar la revisión publicada y esperar a que el catálogo servido coincida; no repetir seguimientos por una lectura atrasada.
- No hay deduplicación duradera entre isolates ni entrega exactamente una vez. GitHub puede sustituir una ejecución pendiente por otra del mismo grupo de concurrencia. Probar operaciones **secuencialmente**, sin búsquedas simultáneas ni dobles solicitudes entre usuarios.
- El bloqueo del Worker dura diez minutos por convocatoria/isolate y no se libera al recibir la confirmación. Esperar ese plazo antes de probar la operación inversa; no reiniciar el Worker para saltarlo.
- Una confirmación Telegram fallida puede marcar el workflow como fallido **después de guardar**. Su aviso genérico de fallo no prueba que SQLite no haya cambiado: revisar el paso de publicación y el catálogo antes de repetir.
- El webhook devuelve HTTP 200 antes de completar el trabajo en `waitUntil`. Errores posteriores no garantizan reentrega de Telegram. Revisar logs y estado ante una solicitud sin respuesta.
- El reparador usa evidencia capturada el 01/11/2026 y valida título/URL, no vigencia. Si cambia la ficha oficial, detenerse y actualizar la evidencia antes de reparar; no aplicar capturas antiguas ciegamente.
- Versiones verificadas aquí: Python 3.12.14, SQLite 3.53.1, Node 24.22.1, pnpm 11.25.0. Actions usa Python 3.12 y Node del runner; no cambia este despliegue. Python fija las dependencias directas salvo `tzdata>=2025.2`, y no hay lock completo de transitivas: registrar `pip freeze`. pnpm usa el lock existente. No se promete una reconstrucción binaria idéntica.
- No se ha inspeccionado el Worker efectivo en Cloudflare ni sus secretos/permisos. Confirmar versión previa recuperable, cuenta correcta, despliegues automáticos y autorizaciones antes de publicar. No añadir infraestructura.

### Procedimiento ejecutable por el propietario

Comandos en **PowerShell**, con `git`, `gh`, Python del entorno local y `pnpm` disponibles. Comprobar `$LASTEXITCODE` después de cada comando nativo: PowerShell no siempre convierte su fallo en excepción. `throw` significa detener el procedimiento; no continuar con el siguiente bloque. Las variables se conservan en la misma terminal.

#### 0. Preparación y pausa real

No usar el bot durante la ventana. Pausar también cualquier automatismo externo o Cloudflare Workers Builds que publique en este Worker; comprobarlo en Cloudflare, ya que no puede inferirse del repositorio. Guardar su estado para restaurarlo después. No modificar secretos.

```powershell
$ErrorActionPreference = 'Stop'
$repo = 'Alarbon/bot_opos'
$source = 'C:\Users\usuario\Desktop\bot oposiciones'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path (Split-Path $source -Parent) "bot_opos-backup-$stamp"
$release = Join-Path (Split-Path $source -Parent) "bot_opos-release-$stamp"
$python = Join-Path $source '.venv\Scripts\python.exe'
New-Item -ItemType Directory -Path $backup | Out-Null
gh auth status
gh api "repos/$repo/actions/workflows" --jq '.workflows[] | [.path,.state] | @tsv'
gh workflow disable oposiciones.yml -R $repo
if ($LASTEXITCODE) { throw 'No se pudo pausar búsqueda' }
gh workflow disable seguimiento.yml -R $repo
if ($LASTEXITCODE) { throw 'No se pudo pausar seguimiento' }
gh api --paginate "repos/$repo/actions/runs?per_page=100" --jq '.workflow_runs[] | select(.status != "completed") | [.id,.name,.status] | @tsv'
```

Guardar qué workflows estaban activos. La última consulta debe quedar vacía; si hay runs, **esperar a que terminen**, usando `gh run watch ID -R $repo --exit-status`. No cancelarlos ni volver a ejecutar runs fallidos durante la pausa. GitHub documenta que deshabilitar no cancela las ejecuciones existentes: [pausar workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).

Comprobar en los últimos runs que `Persist delivery receipts` y los pushes no fallaron. Si hay envíos sin recibo persistido o outbox `SENDING`/`UNCERTAIN`, detenerse para resolver cada evento sin reintento automático. No es motivo para borrar la outbox.

#### 1. Fijar revisión y obtener respaldo verificable

```powershell
git clone --branch main "https://github.com/$repo.git" $release
if ($LASTEXITCODE) { throw 'Falló clon de producción' }
Set-Location $release
$baseline = (git rev-parse HEAD).Trim()
$baseline | Set-Content (Join-Path $backup 'baseline.txt')
Copy-Item -LiteralPath 'data/oposiciones.db' -Destination (Join-Path $backup 'produccion.db')
Copy-Item -LiteralPath 'data/catalog.json' -Destination (Join-Path $backup 'catalog-anterior.json')
Copy-Item -LiteralPath 'data/latest_run.json' -Destination (Join-Path $backup 'latest_run-anterior.json')
git ls-tree HEAD -- data/oposiciones.db data/catalog.json data/latest_run.json |
  Set-Content (Join-Path $backup 'git-blobs.txt')
Get-FileHash (Join-Path $backup 'produccion.db') -Algorithm SHA256 |
  Format-List | Out-File (Join-Path $backup 'sha256.txt')
$env:BOT_DEPLOY_BACKUP = $backup
@'
import os, sqlite3
from pathlib import Path
p = Path(os.environ['BOT_DEPLOY_BACKUP'])
with sqlite3.connect((p/'produccion.db').as_uri()+'?mode=ro', uri=True) as src:
    assert src.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert src.execute('PRAGMA foreign_key_check').fetchall() == []
    assert src.execute('PRAGMA user_version').fetchone()[0] == 6
    assert not src.execute("SELECT 1 FROM notification_outbox WHERE status IN ('SENDING','UNCERTAIN')").fetchall(), 'Resolver entregas inciertas'
    with sqlite3.connect(p/'backup.db') as dst:
        src.backup(dst)
print('Respaldo creado; conservar también el blob original y su SHA256')
'@ | & $python -
if ($LASTEXITCODE) { throw 'Respaldo inválido: detener' }
```

Esperado: base de esquema 6 íntegra, sin relaciones rotas ni entregas inciertas. El clon conserva código/YAML anterior en `$baseline` y el backup está fuera del repositorio. Copiar este directorio a una ubicación privada de respaldo del propietario. `PENDING` o `RETRYABLE`, si aparecen, se preservan y podrán enviarse al hacer la búsqueda real: revisar antes de autorizarla. No ejecutar `db check` sobre el respaldo como sustituto de estas lecturas: abrir `SQLiteStore` puede migrar una base antigua.

#### 2. Integrar únicamente la entrega local en el clon limpio

```powershell
git -C $source diff --binary --output="$backup\codigo.patch" -- . ':(exclude)data'
if ($LASTEXITCODE) { throw 'No se pudo crear patch' }
git apply --check "$backup\codigo.patch"
if ($LASTEXITCODE) { throw 'Conflicto con producción; no aplicar ni resolver a ciegas' }
git apply "$backup\codigo.patch"
if ($LASTEXITCODE) { throw 'Patch fallido' }
$newFiles = @(
  'VALIDACION_Y_DESPLIEGUE.md',
  'oposiciones_bot/sources/iaap_fields.py',
  'scripts/repair_iaap_copy.py',
  'tests/fixtures/iaap_b12000.html',
  'tests/fixtures/iaap_c12003.html',
  'tests/test_iaap_fields.py'
)
foreach ($file in $newFiles) {
  $dest = Join-Path $release $file
  if (Test-Path -LiteralPath $dest) { throw "Revisar fichero nuevo ya existente: $file" }
  Copy-Item -LiteralPath (Join-Path $source $file) -Destination $dest
}
git status --short
git diff --check
```

Esperado: solo los archivos de la entrega enumerados arriba y las modificaciones de código/pruebas ya revisadas. Sin `.env`, imagen preexistente, credenciales ni estado histórico del escritorio. Si el repositorio avanzó con cambios de código que impiden aplicar el patch, detenerse e integrar con revisión humana. No hacer reset, rebase o push forzado.

#### 3. Reparar y validar copias, regenerando el catálogo

El bloque siguiente se ejecuta desde `$release`, usa `backup.db` y genera salidas nuevas fuera del repositorio. No modifica la base clonada ni producción. **No repetirlo en el mismo directorio de backup:** el reparador rechaza salidas existentes. Para repetir todo el ensayo, usar otra carpeta nueva.

```powershell
@'
import hashlib, json, os, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from scripts.repair_iaap_copy import repair_copy
from oposiciones_bot.config import load_config
from oposiciones_bot.db import SQLiteStore
from oposiciones_bot.catalog import export_catalog
p = Path(os.environ['BOT_DEPLOY_BACKUP'])
config = load_config()
base = 'https://portalempleopublico.juntadeandalucia.es/sede/acceso-tramites/seguimiento-procesos-selectivos/'
evidence = {base+'P2B12000': Path('tests/fixtures/iaap_b12000.html'),
            base+'c12003-ayudantes-tecnicos-opcion-informatica': Path('tests/fixtures/iaap_c12003.html')}
def read(path):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
        return {n: [dict(r) for r in db.execute('SELECT * FROM "'+n+'" ORDER BY rowid')]
                for n, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
before = read(p/'backup.db')
assert before == read(p/'produccion.db')
audit = repair_copy(p/'backup.db', p/'reparada.db', evidence, config)
repair_copy(p/'reparada.db', p/'repetida.db', evidence, config)
after = read(p/'reparada.db')
assert after == read(p/'repetida.db'), 'No es idempotente'
assert before.keys() == after.keys()
targets = {x['id'] for x in audit}
assert len(audit) == len(targets) == 2
allowed = {'organisation','normalised_organisation','group_name','positions','access',
           'compatibility','publication_date','deadline','deadline_confirmed','status',
           'semantic_hash','snapshot_json','last_changed'}
changes = {}
for table in before:
    if table != 'processes':
        assert before[table] == after[table], table
        continue
    for a, b in zip(before[table], after[table], strict=True):
        assert a['id'] == b['id']
        fields = {k for k in a if a[k] != b[k]}
        assert not fields or (a['id'] in targets and fields <= allowed)
        if fields: changes[a['id']] = sorted(fields)
store = SQLiteStore(p/'reparada.db')
try:
    export_catalog(store, config, datetime.now(ZoneInfo('Europe/Madrid')).date(), p/'catalog.json')
finally:
    store.close()
assert after == read(p/'reparada.db'), 'Exportar catálogo modificó datos: revisar consolidación'
cat = json.loads((p/'catalog.json').read_text(encoding='utf8'))
assert cat['version'] == 2
ids = [x['id'] for x in cat['processes']]
assert len(ids) == len(set(ids))
dbids = {x['id'] for x in after['processes']}
assert set(ids) <= dbids
identities = [i for x in cat['processes'] for i in [x['id'], *x['aliases']]]
assert len(identities) == len(set(identities)) and set(identities) <= dbids
for f in after['followed_processes']:
    assert any(f['process_id'] in [x['id'], *x['aliases']] and x['followed'] for x in cat['processes'])
old = json.loads((p/'catalog-anterior.json').read_text(encoding='utf8'))
removed = sorted({x['id'] for x in old['processes']} - set(ids))
report = {'tables': {k:len(v) for k,v in after.items()}, 'changes': changes,
          'removed_visible_ids': removed, 'catalogue_count': len(ids), 'idempotent': True,
          'production_sha256': hashlib.sha256((p/'produccion.db').read_bytes()).hexdigest()}
(p/'validacion.json').write_text(json.dumps(report, indent=2), encoding='utf8')
print(json.dumps(report, indent=2))
'@ | & $python -
if ($LASTEXITCODE) { throw 'Datos no aptos: no publicar' }
Get-Content (Join-Path $backup 'validacion.json')
```

Esperado sobre la revisión ensayada: cifras de la tabla anterior; cambios solo en los dos UUID descritos; retirada visible únicamente de `f65b4f61-c2b3-4ced-b55f-76063e9a9280`. Leer también `reparada.audit.json`: comprobar plazas/acceso/estado/plazos y fechas que pasan a desconocidas. Si hay nuevas filas de producción, las cifras pueden aumentar: no imponer las cifras históricas, exigir conservación completa. Cualquier consolidación inesperada durante exportación, desaparición de un seguimiento, cambio fuera de las dos fichas o alias ambiguo **detiene el despliegue**.

Pruebas locales pertinentes, sin red ni Telegram real:

```powershell
& $python -m pytest -o addopts='' -q tests/test_iaap_fields.py tests/test_catalog.py tests/test_telegram.py tests/test_run_report.py
if ($LASTEXITCODE) { throw 'Fallaron pruebas Python' }
node --test cloudflare-worker/src/index.test.mjs
if ($LASTEXITCODE) { throw 'Fallaron pruebas Worker' }
& $python -m pip freeze | Set-Content (Join-Path $backup 'pip-freeze.txt')
```

Validado en esta revisión: **26 pruebas Python** (21 de reparación/Telegram/resumen y 5 de catálogo), cinco avisos SWIG ya conocidos; **56 pruebas Worker**, incluidas cuatro nuevas para catálogo HTTP 404/503 y autorización de grupo con/sin usuario explícito. Las 296 pruebas Python de la entrega siguen como validación anterior; no se ha repetido la auditoría completa. Actions ejecutará su suite completa en la primera búsqueda.

Los dos bloques Python de respaldo/preparación de este documento se ejecutaron literalmente sobre una carpeta de ensayo nueva con el blob remoto capturado: pasaron sus aserciones. Todos los bloques PowerShell se analizaron con el parser de PowerShell sin errores; los comandos remotos de mutación no se ejecutaron.

#### 4. Guardar versión anterior y desplegar primero el Worker

```powershell
Set-Location (Join-Path $release 'cloudflare-worker')
pnpm install --frozen-lockfile
if ($LASTEXITCODE) { throw 'Lock o instalación fallidos' }
pnpm exec wrangler whoami
pnpm exec wrangler deployments list
pnpm exec wrangler versions list
pnpm exec wrangler secret list
```

Comprobar cuenta, Worker `oposiciones-telegram`, ruta real, versión activa previa y nombres de secretos. Guardar **el ID de versión activa**, no el ID de deployment, en `$previousWorkerVersion` y en el respaldo. Si hay despliegue gradual con varias versiones activas, detenerse y preparar recuperación de esa distribución. No imprimir valores secretos. Confirmar que los IDs de usuario/chat existentes corresponden a la autorización deseada. Para grupos sin `TELEGRAM_USER_ID` existente, **NO APTO para ese grupo bajo la restricción de no alterar secretos**: resolver con el propietario en otra intervención.

```powershell
# Asignar antes el ID de versión ACTIVA obtenido arriba.
if (-not $previousWorkerVersion) { throw 'Falta versión de recuperación del Worker' }
$previousWorkerVersion | Set-Content (Join-Path $backup 'worker-version-anterior.txt')
pnpm exec wrangler deploy
if ($LASTEXITCODE) { throw 'Despliegue Worker fallido; detener' }
(Invoke-WebRequest "$env:WORKER_URL/health").Content
pnpm exec wrangler deployments list
```

`WORKER_URL` debe contener la URL HTTPS real ya usada en producción. Esperado: salud `ok`, versión nueva al 100%, bindings/rutas conservados. `/health` solo valida servicio, no credenciales externas. Los workflows siguen pausados. El Worker nuevo puede leer el catálogo anterior. No pulsar botones de búsqueda/seguimiento durante este intervalo.

#### 5. Publicar código y estado juntos, sin sobrescribir avances

Desde `$release`, comparar SHA y runs activos otra vez; si hay escritores o el SHA cambió, **abandonar esta candidata y repetir respaldo y reparación sobre el nuevo SHA**. No hacer pull/rebase sobre un estado SQLite preparado.

```powershell
Set-Location $release
$remote = (gh api "repos/$repo/git/ref/heads/main" --jq '.object.sha').Trim()
if ($LASTEXITCODE -or $remote -ne $baseline) { throw 'Producción avanzó; rehacer copia' }
gh api --paginate "repos/$repo/actions/runs?per_page=100" --jq '.workflow_runs[] | select(.status != "completed") | [.id,.status] | @tsv'
```

Solo con salida vacía e informe de datos aceptado, el propietario incorpora **localmente en este clon** la candidata preparada. Esta es la acción deliberada de publicación de datos; no usar la SQLite del escritorio ni la del ensayo de esta sesión.

```powershell
Copy-Item -LiteralPath (Join-Path $backup 'reparada.db') -Destination 'data/oposiciones.db'
Copy-Item -LiteralPath (Join-Path $backup 'catalog.json') -Destination 'data/catalog.json'
git add -- oposiciones_bot scripts tests cloudflare-worker VALIDACION_Y_DESPLIEGUE.md data/oposiciones.db data/catalog.json
if ($LASTEXITCODE) { throw 'No se pudo preparar el commit' }
git diff --cached --name-only
git diff --cached --check
if ($LASTEXITCODE) { throw 'Revisión de diff fallida' }
git diff --cached --stat
# Leer la lista: sin secretos, sin .github/config ajenos, sin latest_run histórico.
git commit -m 'Corregir extracción y activar navegación Telegram con estado conservado'
if ($LASTEXITCODE) { throw 'Commit local fallido' }
$published = (git rev-parse HEAD).Trim()
git push origin HEAD:main
if ($LASTEXITCODE) { throw 'Push rechazado o ambiguo; inspeccionar remoto antes de continuar' }
$remote = (gh api "repos/$repo/git/ref/heads/main" --jq '.object.sha').Trim()
if ($LASTEXITCODE -or $remote -ne $published) { throw 'No coincide la revisión publicada' }
$published | Set-Content (Join-Path $backup 'publicado.txt')
```

Estos commits/push son acciones futuras **del usuario**, nunca ejecutadas por el agente. Un push sin fuerza protege además ante una escritura entre comprobación y publicación. Si falla de forma ambigua, consultar remoto; no asumir que no se publicó. No usar `scripts/publish_state.py` para esta publicación manual: incluye sus propios reintentos/rebase y no publica el código.

Comprobar en GitHub, para `$published`, hashes de `data/oposiciones.db` y `data/catalog.json` contra la candidata, ambos workflows con el grupo común, `latest_run.json` conservado y catálogo público actualizado. Obtener el JSON servido:

```powershell
Invoke-WebRequest "https://raw.githubusercontent.com/$repo/$published/data/oposiciones.db" -OutFile (Join-Path $backup 'publicada.db')
Invoke-WebRequest "https://raw.githubusercontent.com/$repo/$published/data/catalog.json" -OutFile (Join-Path $backup 'catalog-publicado.json')
if ((Get-FileHash (Join-Path $backup 'publicada.db')).Hash -ne (Get-FileHash (Join-Path $backup 'reparada.db')).Hash) { throw 'SQLite publicada distinta' }
if ((Get-FileHash (Join-Path $backup 'catalog-publicado.json')).Hash -ne (Get-FileHash (Join-Path $backup 'catalog.json')).Hash) { throw 'Catálogo publicado distinto' }
$served = Invoke-RestMethod "https://raw.githubusercontent.com/$repo/main/data/catalog.json?fresh=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
$served.version
$served.processes | Select-Object id, followed, positions, access
```

Esperado versión 2, mismos UUID que `catalog.json` preparado y seguimiento original. Si el caché sirve la revisión anterior, esperar y volver a leer; no disparar operaciones mientras difiera. No hay que ejecutar `catalog` sobre otra SQLite ni volver a publicar Actions por separado.

#### 6. Configurar Telegram y comprobar lectura antes de habilitar escritores

Con variables privadas existentes; no crear ni modificar secretos. Registrar URL, `allowed_updates` y pendientes del webhook anterior en una ubicación privada antes de cambiarlo. `getWebhookInfo` puede contener URL sensible: no volcar la respuesta completa en logs compartidos.

```powershell
$tg = "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN"
$webhookBefore = (Invoke-RestMethod -Method Post -Uri "$tg/getWebhookInfo").result
$webhookBefore | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $backup 'webhook-anterior-PRIVADO.json')
Invoke-RestMethod -Method Post -Uri "$env:WORKER_URL/admin/configure" -Headers @{Authorization="Bearer $env:WEBHOOK_SECRET"}
$webhookAfter = (Invoke-RestMethod -Method Post -Uri "$tg/getWebhookInfo").result
$webhookAfter | Select-Object allowed_updates,pending_update_count,last_error_date,last_error_message
```

Esperado `configured`, URL exacta `$env:WORKER_URL/telegram`, actualizaciones `message` y `callback_query`. No se usa `drop_pending_updates`: no descartar mensajes pendientes. Pendientes antiguos y errores se investigan; comparar fecha del error para distinguir un error previo de uno nuevo. Un 502 puede significar que `setWebhook` funcionó y `setMyCommands` falló: comprobar estado real antes de repetir. Contrato: [Telegram Bot API](https://core.telegram.org/bots/api#setwebhook).

Ahora el usuario prueba `/start`, `/convocatorias`, detalle, regreso, fuente y copiar comando. Estas acciones sí generan respuestas reales del bot, solo por el propietario. **No deben crear ningún run de Actions.** Copiar debe producir `/detalle UUID_COMPLETO`; abrirlo debe recuperar la misma ficha. Una URL oficial ausente se omite, un dato desconocido se presenta como no confirmado. Catálogo inaccesible termina con error comprensible y no inicia una búsqueda por sí solo.

Paginación: hay solo tres fichas en la candidata actual, por lo que no existe segunda página real; anterior/siguiente y página caducada están comprobados con seis fichas simuladas. No crear convocatorias falsas para comprobarlo en producción. Probar un botón antiguo que conserve UUID/alias; otro retirado, si existe, debe remitir al listado sin solicitar workflow. En el chat autorizado, otro usuario no debe acceder; no necesita ejecutar comandos en chats ajenos para comprobar la protección ya probada localmente.

#### 7. Habilitar seguimiento y comprobar persistencia de extremo a extremo

```powershell
gh workflow enable seguimiento.yml -R $repo
gh api "repos/$repo/actions/workflows/seguimiento.yml" --jq '.state'
```

Esperado `active`. Búsqueda permanece deshabilitada. Elegir una ficha **no seguida** y anotar su UUID. Pulsar Seguir una vez: respuesta pendiente, un run de `seguimiento.yml`, publicación correcta y después confirmación. Consultar `/seguimientos` y el JSON público: UUID correcto, `followed=true`; sin pérdida del seguimiento previo. Revisar ejecución con `gh run list -R $repo --workflow seguimiento.yml --limit 5` y `gh run view ID -R $repo`.

Esperar al menos diez minutos desde la primera solicitud antes de pulsar Dejar de seguir para esa misma ficha. Confirmar `followed=false`, otra publicación y preservación del seguimiento inicial. No modificar el seguimiento original para la prueba. Si una confirmación no llega, inspeccionar los pasos y el catálogo: el guardado puede haberse completado. No pulsar repetidamente ni repetir una solicitud con respuesta incierta. Descargar/verificar la nueva SQLite fijada al commit resultante para comprobar tabla `followed_processes` y outbox, que debe seguir intacta.

#### 8. Habilitar búsqueda y ejecutar una única búsqueda real

Solo si las pruebas anteriores pasaron y no hay ningún run activo ni entregas inciertas:

```powershell
gh workflow enable oposiciones.yml -R $repo
gh api "repos/$repo/actions/workflows/oposiciones.yml" --jq '.state'
gh workflow run oposiciones.yml -R $repo --ref main -f dry_run=false
gh run list -R $repo --workflow oposiciones.yml --event workflow_dispatch --limit 5
# Identificar la nueva ejecución; no volver a solicitarla si tarda en aparecer.
gh run watch ID -R $repo --exit-status
gh run view ID -R $repo
```

Esta búsqueda real **envía Telegram**, incluidos pendientes/reintentos y resumen; queda exclusivamente para el propietario. Elegir hora alejada del cron (08:17 Europe/Madrid) y no pulsar `/buscar` a la vez. Comprobar suite Actions completa, persistencia de recogida, claim previo al envío y recibos posteriores; último informe `run_id` igual al run elegido; `/estado` distingue `success` de fuentes PARTIAL/ERROR. BOJA no debe ocultar un nuevo 400; IAAP/BOP pueden seguir parciales por causas de origen. Si alguna fuente sigue parcial, se acepta solo como limitación explícita, con checkpoints sin avance indebido. Ninguna fuente correcta → detener, no repetir en bucle.

Respaldar de nuevo la SQLite del último commit y revisar integridad, relaciones, los 24 UUID iniciales presentes, seguimiento original y outbox (nuevas filas justificadas por esa búsqueda; ninguna anterior perdida). Si queda `SENDING`/`UNCERTAIN`, fallo de persistencia o rechazo de publicación, pausar y resolver antes de otra búsqueda. Restaurar únicamente los automatismos que estaban activos al comienzo y guardar el SHA final y versión Worker. Los cron y el gate horario existentes no se cambian.

### Recuperación exacta según el punto de fallo

1. **Antes de publicar GitHub:** dejar workflows pausados. Si se cambió el Worker y falla su prueba, volver al ID guardado:

   ```powershell
   Set-Location (Join-Path $release 'cloudflare-worker')
   pnpm exec wrangler rollback $previousWorkerVersion
   if ($LASTEXITCODE) { throw 'Rollback fallido; mantener pausa y revisar Cloudflare' }
   (Invoke-WebRequest "$env:WORKER_URL/health").Content
   pnpm exec wrangler deployments list
   ```

   Cloudflare exige ID de **versión** y cambia inmediatamente el deployment activo: [recuperación de Worker](https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/). Confirmar bindings/rutas. En esta entrega no se crean ni destruyen KV/D1. No usar un rollback implícito a «la anterior» sin verificar el ID.

2. **Código/datos publicados, sin búsquedas ni seguimientos posteriores:** no restaurar SQLite por defecto. Esquema 6 y reparación son legibles por el código anterior. En un clon nuevo de `main`, recuperar únicamente los archivos de código cambiados desde `$baseline` y publicar un commit compensatorio. Con los workflows aún pausados y revisión humana:

   ```powershell
   # Ejecutar en un clon NUEVO de main, nunca sobre el árbol de trabajo original.
   $rollbackCodePaths = @(git diff --name-only --diff-filter=AM $baseline $published -- oposiciones_bot scripts tests cloudflare-worker)
   git restore --source=$baseline --staged --worktree -- $rollbackCodePaths
   if ($LASTEXITCODE) { throw 'No se pudo recuperar el código' }
   # Esto retira también los archivos de código/pruebas añadidos por la entrega.
   # No incluye data/, secretos ni archivos no versionados del propietario.
   git diff --cached --name-only
   git diff --cached --check
   if ($LASTEXITCODE) { throw 'Diff de recuperación inválido' }
   & $python -m pytest -o addopts='' -q
   if ($LASTEXITCODE) { throw 'Fallaron pruebas del código recuperado' }
   node --test cloudflare-worker/src/index.test.mjs
   if ($LASTEXITCODE) { throw 'Fallaron pruebas Worker recuperado' }
   git commit -m 'Recuperar código anterior conservando el estado actual'
   if ($LASTEXITCODE) { throw 'No publicar recuperación incompleta' }
   git push origin HEAD:main
   ```

   Revisar que `data/` no aparece en el commit compensatorio y que no se pisan cambios de código posteriores de otra persona. Retirar las pruebas nuevas evita que pytest las descubra contra el código antiguo. Ejecutar la suite del código recuperado antes del commit. Regenerar catálogo con ese código sobre **copia de la SQLite más reciente**, validar conservación como arriba y publicar solo si hace falta; el catálogo versión 2 nuevo ya es estructuralmente compatible. Desplegar/recuperar el Worker anterior solo después de detener todos los emisores de teclados nuevos. Los mensajes con botones nuevos seguirán existiendo: con el Worker anterior no funcionarán; usar comandos de texto o mantener el Worker nuevo si no es la causa del fallo.

3. **Tras operaciones reales:** repetir pausa y drenaje, obtener nuevo backup y conservar la SQLite **más reciente**, con sus seguimientos y recibos. Recuperar código como en el punto 2 y Worker solo si procede. No hacer `git revert` del commit completo de entrega ni restaurar `data/` desde `$baseline`: eso perdería escrituras. No ejecutar `outbox retry` sobre `SENT`, `SENDING` o `UNCERTAIN` por conjetura.

4. **Si el problema es la reparación de datos:** no restaurar el archivo completo. Comparar el `.audit.json` con la fila actual y preparar corrección de campos sobre una copia del último backup. Restaurar un campo anterior solo si su valor actual sigue siendo el valor reparado y no hay observaciones posteriores. Validar UUID/tablas/outbox, exportar catálogo y publicar durante otra pausa. Si no se puede demostrar esa condición, detenerse para revisión manual. El backup íntegro se reserva para desastre confirmado y reconciliación de escrituras; no hay un comando universal seguro que mezcle dos SQLite divergentes.

5. **Webhook:** normalmente se conserva URL/secreto y solo se amplían tipos de actualización; dejar el webhook registrado no pierde datos. Si se vuelve al Worker anterior y hay que restaurar la suscripción, usar la configuración guardada (mismo secreto privado):

   ```powershell
   $oldHook = Get-Content (Join-Path $backup 'webhook-anterior-PRIVADO.json') -Raw | ConvertFrom-Json
   if (-not $oldHook.url) { throw 'Antes no había webhook; recuperación manual necesaria' }
   if (-not $oldHook.allowed_updates) { throw 'Falta lista anterior; revisar configuración antes de restaurar' }
   $hookBody = @{url=$oldHook.url; secret_token=$env:WEBHOOK_SECRET; allowed_updates=@($oldHook.allowed_updates)} | ConvertTo-Json
   Invoke-RestMethod -Method Post -Uri "$tg/setWebhook" -ContentType 'application/json' -Body $hookBody
   ```

   Comprobar `ok=true` y `getWebhookInfo`; no descartar pendientes ni rotar secretos. Los comandos publicados con `setMyCommands` siguen siendo compatibles; no es necesario cambiarlos para volver al código anterior.

Después de cualquier recuperación: salud, catálogo/IDs y comandos de lectura correctos antes de habilitar seguimiento, y seguimiento confirmado antes de habilitar búsqueda. Restaurar el estado de activación anterior de cada workflow; si el fallo no está entendido, mantenerlos pausados y registrar la incidencia.

### Conclusión

**APTO PARA DESPLIEGUE CONTROLADO**, con pausa y drenaje efectivos, nuevo respaldo fijado a commit, conservación e idempotencia aprobadas, Worker previo recuperable y autorización existente correcta. La preparación está ensayada sobre una copia reciente real; todavía no están verificados el Worker efectivo, permisos/secretos desplegados, configuración de Telegram ni la entrega y confirmación reales. Si cualquiera de esas puertas falla, el procedimiento se detiene sin habilitar la búsqueda. No es una autorización para desplegar automáticamente ni una garantía de cobertura completa de las fuentes.
