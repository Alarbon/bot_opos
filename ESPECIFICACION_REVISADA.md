# Especificación revisada — Bot de oposiciones de informática

**Versión contractual:** 1.0  
**Ámbito:** primera versión funcional y verificable  
**Zona horaria de presentación:** `Europe/Madrid`  
**Persistencia interna de fechas:** UTC e ISO 8601

> Este documento define el contrato objetivo y sus criterios de aceptación. El
> estado realmente implementado, las fuentes degradadas y los límites pendientes
> se describen en `README.md`; una casilla sin marcar no debe interpretarse como
> una capacidad ya verificada.

## 1. Propósito

Construir un bot en Python que consulte fuentes oficiales de empleo público,
identifique oportunidades relacionadas con informática compatibles con un perfil
de FP de Grado Superior y envíe por Telegram únicamente:

- una oportunidad nueva;
- un cambio material de una oportunidad ya conocida;
- un recordatorio de plazo confirmado;
- una alerta de revisión cuando no haya evidencia suficiente para decidir.

El objetivo es reducir de forma drástica la búsqueda manual. La v1 **no promete
cobertura absoluta de Internet ni sustituye la lectura de las bases oficiales**.
Las sedes pueden cambiar de estructura, limitar robots, requerir JavaScript,
CAPTCHA o autenticación, o publicar información no indexada. El bot debe hacer
visible esa degradación; nunca debe fingir que una fuente se comprobó con éxito.

## 2. Garantías y límites de la v1

La v1 debe garantizar:

1. Persistencia entre ejecuciones.
2. Identidad estable de los expedientes conocidos.
3. Ausencia de notificaciones repetidas por una simple redetección.
4. Detección de cambios semánticos relevantes.
5. Clasificación de titulación en `COMPATIBLE`, `NO_COMPATIBLE` o `REVISAR`.
6. Aislamiento de fallos: una fuente caída no detiene las demás.
7. Trazabilidad: toda decisión conserva fuente, URL y evidencia disponible.
8. Ejecución local y programada, con modo sin envío real.

La v1 no garantiza:

- que todas las administraciones publiquen a tiempo o en páginas accesibles;
- que un PDF escaneado sin capa de texto pueda analizarse sin OCR;
- una interpretación jurídica de equivalencias de títulos;
- entrega exactamente una vez en Telegram;
- cálculo legal exacto de plazos expresados solo en días hábiles;
- acceso a zonas protegidas por login, CAPTCHA o restricciones de rastreo;
- inferir que una convocatoria terminó porque desapareció de una lista.

Ante la duda, se conserva la oportunidad como `REVISAR`. Solo se descarta de
forma automática cuando exista evidencia clara de incompatibilidad.

## 3. Perfil objetivo

Titulaciones prioritarias:

- DAM / Desarrollo de Aplicaciones Multiplataforma.
- DAW / Desarrollo de Aplicaciones Web.
- ASIR / Administración de Sistemas Informáticos en Red.
- Titulaciones equivalentes de la familia Informática y Comunicaciones.

También se consideran potencialmente compatibles requisitos generales como:

- Bachiller o Técnico;
- Técnico Superior o FP de Grado Superior;
- ESO o equivalente;
- subgrupos C1 y C2;
- grupo B cuando las bases permitan Técnico Superior.

Se rechazan únicamente los procesos que exijan de forma obligatoria y exclusiva
una titulación universitaria incompatible. La mera aparición de expresiones como
«Ingeniería Informática» en el temario, los méritos, el tribunal o una lista de
titulaciones no basta para excluir.

## 4. Ámbito funcional

### 4.1 Procesos incluidos

- oposición y concurso-oposición;
- acceso libre y procesos mixtos con plazas de acceso libre;
- bolsas, ampliaciones de bolsa e interinidades;
- personal laboral temporal o fijo;
- estabilización con acceso libre;
- convocatoria, apertura o reapertura de plazo;
- correcciones y modificaciones de bases;
- listas de personas admitidas y excluidas;
- fecha, hora o lugar de examen;
- plantillas, notas, aprobados y destinos.

### 4.2 Procesos excluidos

- promoción interna exclusivamente;
- puestos sin relación suficiente con informática;
- procesos inequívocamente incompatibles por titulación;
- procesos cerrados y antiguos descubiertos durante el arranque, salvo que
  publiquen un hito nuevo que siga siendo útil;
- contratación pública de suministros o servicios, cursos, ayudas y noticias
  que contengan palabras informáticas pero no sean selección de personal.

### 4.3 Geografía

Prioridad máxima: Martos.  
Prioridad alta: Torredonjimeno, Torredelcampo, Jaén, Diputación de Jaén y
Universidad de Jaén.  
Cobertura cercana configurable: Jamilena, Los Villares, Fuensanta de Martos,
Alcaudete, Porcuna, Villardompardo, Fuerte del Rey, Mancha Real, Mengíbar,
Arjona, La Guardia de Jaén y Valdepeñas de Jaén.

El radio se calcula contra una tabla local y versionada de coordenadas. No se
depende de geocodificación remota durante cada ejecución.

Excepciones obligatorias al filtro geográfico:

- TAI del Estado;
- cuerpos, escalas o categorías informáticas de la Junta de Andalucía.

Una oportunidad de la provincia de Jaén cuya localidad no pueda determinarse se
marca `REVISAR`; no se descarta.

## 5. Matriz de cobertura de fuentes

Niveles de cobertura:

- **A — estructurada:** API o índice oficial con identificadores estables.
- **B — directa best effort:** HTML/PDF público; sensible a cambios de diseño.
- **C — indirecta o degradada:** la vía directa está restringida o es
  incompleta; se depende parcialmente de boletines y otras fuentes.

| Fuente | Nivel v1 | Activa por defecto | Método | Cobertura y limitación explícita |
|---|---:|:---:|---|---|
| BOE | A | Sí | API oficial de sumario diario y documentos enlazados | Fuente autoritativa para TAI y aperturas publicadas en BOE. Un anuncio puede ser actualización de bases ya vistas en BOP. |
| BOJA | A | Sí | API oficial de datos abiertos y documentos | Cubre disposiciones publicadas; no sustituye los hitos que solo aparezcan en IAAP. |
| BOP de Jaén | A/B | Sí | Índice diario, edictos y PDF oficial | Fuente principal local. La extracción de PDF puede quedar en `REVISAR` si no existe texto utilizable. |
| IAAP / portal de empleo público andaluz | B | Sí | Listados y fichas públicas | Necesario para hitos posteriores. El HTML puede cambiar y debe quedar monitorizado. |
| Punto de Acceso General | C | No | Buscador público, solo en ventanas y ritmo permitidos | Fuente complementaria. Su cobertura local no es total y sus reglas de rastreo exigen un uso especialmente conservador. No se usa para afirmar cobertura municipal completa. |
| Ayuntamiento de Martos | B | Sí | Páginas públicas de empleo y documentos | Puede adelantar publicaciones del BOP. Si falla, BOP/BOE aportan cobertura parcial, no equivalente. |
| Ayuntamiento de Torredonjimeno | C | Sí, solo página permitida | Portada pública permitida y boletines | El tablón restringido por `robots.txt` no se elude. BOP/BOE son el respaldo, con posible retraso o pérdida de bolsas no publicadas allí. |
| Ayuntamiento de Torredelcampo | B | Sí | Página pública de ofertas y documentos | Best effort; cambios de plantilla deben producir fallo visible, no cero resultados silencioso. |
| Ayuntamiento de Jaén | B/C | Sí | Portal público de empleo y boletines | Si requiere sesión, JavaScript no soportado o CAPTCHA, se degrada a cobertura BOP/BOE. |
| Diputación Provincial de Jaén | A/B | Sí | Servicio público estructurado, páginas y BOP | Se deben conservar referencias cruzadas al BOP para evitar duplicados. |
| Universidad de Jaén | B | Sí | Anuncios públicos PTGAS/PAS y documentos | Puede publicar varias categorías en un mismo anuncio; el parser debe poder emitir más de un candidato. |

Una fuente solo se considera **implementada** cuando cumple todos estos puntos:

1. Consulta una URL oficial real y permitida.
2. Maneja paginación o ventana temporal cuando corresponda.
3. Devuelve un identificador de origen, título, fecha y URL trazable.
4. Tiene al menos un fixture reproducible y una prueba del parser.
5. Registra inicio, fin, cantidad de elementos y error.
6. Distingue «sin resultados» de «no se pudo comprobar».
7. No es una clase vacía ni una función que siempre devuelve `[]`.

El estado de cada fuente (`OK`, `DEGRADED`, `FAILED`, `DISABLED`) debe aparecer
en los logs de la ejecución. Una fuente sin resultados durante un periodo
inusual puede considerarse degradada aunque la respuesta HTTP sea 200.

## 6. Restricciones de acceso y rastreo

Antes de activar una fuente se revisan sus condiciones públicas y `robots.txt`.
La aplicación:

- no elude CAPTCHA, login, controles anti-bot ni rutas prohibidas;
- utiliza un `User-Agent` identificable;
- limita frecuencia y concurrencia por dominio;
- aplica timeout, reintentos con espera y `Retry-After`;
- reutiliza sesión HTTP y, cuando sea posible, `ETag` y `Last-Modified`;
- no descarga de nuevo un documento cuyo hash ya conoce;
- impone límite configurable al tamaño de HTML/PDF;
- valida código HTTP, tipo de contenido y redirección antes de parsear;
- registra como degradada una fuente bloqueada en vez de simular éxito.

Si una ruta deja de estar permitida, se desactiva ese adaptador y se documenta
el respaldo disponible. Un respaldo indirecto nunca se presenta como cobertura
idéntica a la fuente directa.

## 7. Arquitectura

```text
SourceAdapter
    -> RawItem inmutable
    -> extracción/enriquecimiento HTML o PDF
    -> Candidate (uno o varios por publicación)
    -> normalización
    -> clasificadores
    -> resolución de identidad
    -> expediente canónico + observación
    -> comparación semántica
    -> evento persistente
    -> notification_outbox
    -> Telegram
```

Responsabilidades:

- `sources/`: adquirir datos, sin decidir compatibilidad.
- `parsers/` y `enrichment`: extraer texto y campos con evidencia.
- `classifiers`: relevancia, titulación, acceso, geografía y prioridad.
- `matching`: referencias, huellas estables y similitud conservadora.
- `db`: transacciones, expedientes, orígenes, eventos y recordatorios.
- `notifications`: outbox, formato Telegram y política de entrega.
- `app`: orquestar fuentes de forma aislada y producir resumen.

Un elemento de origen puede producir cero, uno o varios candidatos. Una
resolución que enumera varias categorías no debe convertirse por fuerza en un
único expediente.

## 8. Modelo persistente

### 8.1 Expediente canónico

`processes` representa la oposición o bolsa, no una página concreta. Contiene:

- UUID interno;
- título y organismo originales y normalizados;
- firma de puesto/cuerpo;
- localidad, provincia y ámbito;
- grupo, plazas y acceso;
- compatibilidad y evidencia de titulación;
- estado actual;
- publicación inicial, plazo y fecha de examen;
- indicadores de plazo confirmado y proceso especial;
- primer y último avistamiento;
- snapshot y hash semántico actuales.

### 8.2 Registros de fuente

`source_items` conserva como mínimo:

- fuente y `external_id`, únicos en conjunto;
- expediente asociado;
- referencia, URL y `raw_hash`;
- primer y último avistamiento.

Las referencias oficiales se almacenan aparte y se normalizan por espacio de
nombres (`BOE`, `BOJA`, `BOP_JAEN`, `PAG`, `IAAP`, expediente local). Un
expediente puede tener muchas URLs y referencias.

### 8.3 Eventos y salida

La base también conserva:

- eventos deterministas de nueva convocatoria, actualización, revisión y
  recordatorio;
- `notification_outbox` con estado e intentos;
- recordatorios enviados, incluyendo la fecha límite concreta;
- resumen de ejecuciones por fuente.

Todas las escrituras de expediente y creación de evento se realizan en una
misma transacción SQLite.

## 9. Identidad estable y deduplicación

### 9.1 Regla fundamental

El `process_id` es un UUID persistente. Una huella sirve para localizar un
expediente, pero no sustituye su identidad.

No se usa `hash()` de Python porque no es estable entre procesos. Toda huella se
calcula con SHA-256 sobre JSON canónico:

```python
sha256(
    json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
```

La identidad no puede depender únicamente de:

- URL;
- número de plazas;
- fecha límite;
- estado;
- fecha de examen;
- `last_seen`;
- texto bruto completo.

Esos valores cambian durante la vida del mismo proceso.

### 9.2 Orden de resolución

1. Coincidencia exacta de `(source, external_id)`.
2. Coincidencia exacta de referencia oficial normalizada.
3. Coincidencia con una referencia citada dentro de otro anuncio. Ejemplo: un
   BOE que cite número y fecha del BOP de las bases.
4. Coincidencia de una firma canónica fuerte: organismo, cuerpo/puesto,
   convocatoria u OEP, acceso y grupo.
5. Comparación difusa solo entre candidatos previamente bloqueados por mismo
   organismo y sin conflictos de grupo/acceso.

Una similitud de título de al menos `0.92`, con mismo organismo y sin conflictos
duros, permite unión automática. Por debajo del umbral se crea expediente
separado o se marca como posible duplicado; nunca se fusiona solo porque dos
títulos contienen «Técnico Informático».

Deben permanecer separados:

- convocatorias de años o resoluciones de origen distintos;
- acceso libre y promoción interna exclusiva;
- puestos diferentes incluidos en una misma publicación;
- procesos con requisitos o bases distintas aunque el nombre se parezca.

### 9.3 Relaciones entre fuentes

La secuencia BOP → BOE → sede → PAG representa normalmente un solo expediente.
Añadir una URL espejo o una nueva fuente no genera por sí mismo una notificación.
Sí puede generarla el hito que esa fuente aporta, por ejemplo la apertura del
plazo en BOE.

## 10. Clasificación

### 10.1 Relevancia informática

Se exige:

- una señal fuerte de puesto/cuerpo informático, o una combinación suficiente
  de señales débiles; y
- una señal de selección de personal público.

`sistemas`, `TIC`, `digital`, `informática` o `programa` de forma aislada no son
suficientes. TAI y los códigos configurados de informática de la Junta son
señales especiales fuertes.

### 10.2 Titulación

- `COMPATIBLE`: un apartado obligatorio admite expresamente una titulación del
  perfil o un nivel general configurado como válido.
- `NO_COMPATIBLE`: el requisito obligatorio exige exclusivamente universidad y
  no contiene alternativa compatible.
- `REVISAR`: no se encuentra el apartado, hay remisión normativa, el PDF no es
  extraíble o existen señales contradictorias.

La decisión conserva un fragmento de evidencia y, cuando sea posible, documento
y página. No se infiere incompatibilidad a partir de una mención fuera del
apartado de requisitos.

### 10.3 Acceso

- Libre: incluir.
- Mixto con plazas libres: incluir.
- Promoción interna exclusivamente: excluir.
- Desconocido: `REVISAR`.

## 11. Cambios relevantes

Se mantienen dos hashes:

- `raw_hash`: bytes o texto bruto, para evitar reprocesado innecesario;
- `semantic_hash`: representación normalizada de los campos relevantes.

Generan actualización:

- apertura, reapertura o cambio de plazo;
- variación de plazas o distribución por turno;
- cambio de acceso, grupo o requisito de titulación;
- corrección, modificación, suspensión o anulación;
- listas provisionales o definitivas;
- fecha, hora o lugar de examen;
- plantilla, notas, aprobados o destinos;
- ampliación de bolsa;
- nuevas bases o corrección oficial cuyo efecto no se pueda extraer y requiera
  revisión.

No generan actualización:

- mayúsculas, tildes de comparación, puntuación o espacios;
- orden distinto de campos equivalentes;
- nueva URL espejo sin contenido nuevo;
- cambios de navegación, pie, cookies o contacto;
- metadatos del PDF;
- `last_seen`;
- un `raw_hash` nuevo cuyo snapshot semántico sea igual.

La clave de evento es determinista, por ejemplo:

```text
SHA256(process_id | event_kind | semantic_payload | effective_date)
```

Una observación antigua no puede hacer retroceder el estado. La desaparición de
una lista tampoco cierra un proceso. Además de las fases habituales se admiten
`DESCONOCIDA`, `SUSPENDIDA`, `ANULADA` y `REABIERTA`.

## 12. Política de primera ejecución

Sin una política de arranque, el bot enviaría como nuevas convocatorias antiguas.
La v1 aplica estas reglas:

1. Consulta una ventana retrospectiva configurable, con un mínimo operativo de
   varios días y solapamiento entre ejecuciones.
2. En el primer arranque solo notifica procesos todavía abiertos, hitos actuales
   o publicaciones dentro de `bootstrap_notify_days`.
3. Los procesos antiguos o cerrados se pueden sembrar en la base sin Telegram.
4. Un proceso sembrado puede generar después una actualización si aparece un
   hito material.
5. El cursor de una fuente solo avanza después de completar satisfactoriamente
   todas sus páginas.
6. Las consultas posteriores solapan días para capturar publicaciones indexadas
   con retraso.

La fecha de primera observación no se presenta como fecha oficial de publicación.

## 13. Outbox y política de entrega

Telegram no ofrece una clave de idempotencia de cliente. Por ello no es posible
garantizar simultáneamente «cero duplicados» y «cero pérdidas» ante una caída en
el instante exacto entre el envío remoto y la confirmación local.

La v1 prioriza **no duplicar automáticamente** mediante una política
`at_most_once`:

1. El evento se inserta en `notification_outbox` dentro de la transacción de
   ingesta.
2. El emisor reclama el evento y lo marca `SENDING` antes de llamar a Telegram.
3. Respuesta confirmada de Telegram: `SENT`, conservando `message_id`.
4. Rechazo inequívoco antes de aceptación, como un 429: `RETRYABLE`, respetando
   `Retry-After`.
5. Timeout, desconexión o caída con resultado remoto desconocido: `UNCERTAIN`.
   No se reintenta automáticamente porque podría duplicar.
6. Un operador puede revisar Telegram y elegir reintentar o suprimir un evento
   `UNCERTAIN`. El reintento manual acepta explícitamente el riesgo de duplicado.

Consecuencia documentada: en el caso remoto ambiguo, la política puede perder un
aviso antes que enviar dos. Esta es una limitación deliberada y observable, no
una garantía falsa de entrega exactamente una vez.

El modo `dry-run` genera y registra mensajes sin llamar a Telegram.

## 14. Recordatorios

Solo se generan para una fecha límite explícita y confirmada. Una regla como
«20 días hábiles desde el día siguiente» puede almacenarse como texto, pero no
activa recordatorios hasta disponer de fecha fiable.

La clave única es:

```text
(process_id, deadline_date, days_before)
```

Así, una nueva fecha oficial puede producir recordatorios nuevos sin repetir los
de la fecha anterior. No se recuerda un proceso finalizado, anulado, suspendido
o con plazo cerrado.

## 15. PDFs y documentos

Proceso mínimo:

1. Validar URL, respuesta, MIME y tamaño.
2. Calcular SHA-256 del documento.
3. Extraer texto con una librería local.
4. Detectar si la capa de texto es vacía o insuficiente.
5. Extraer requisitos, acceso, plazas, grupo, plazo y eventos.
6. Conservar URL, hash y estado de extracción.

El OCR no es requisito de la v1. Un PDF escaneado se marca `REVISAR` y mantiene
su enlace oficial. El sistema no debe descartar silenciosamente una oportunidad
porque la extracción falló.

## 16. Telegram

Credenciales obligatorias solo mediante entorno:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

No se escriben en código, configuración versionada, URLs de logs ni trazas de
excepción. Los mensajes usan un formato escapado y muestran:

- tipo: nueva, actualización, revisión o recordatorio;
- organismo y puesto;
- localidad/ámbito;
- compatibilidad y evidencia breve;
- plazas, grupo y acceso cuando existan;
- publicación, examen y plazo cuando estén confirmados;
- prioridad;
- todos los enlaces oficiales conocidos, sin duplicarlos.

Varios cambios del mismo expediente detectados en una ejecución se agrupan en
una sola actualización.

## 17. Persistencia y GitHub Actions

SQLite es válido para uso local y para un MVP de un único escritor. En GitHub
Actions el runner es efímero, por lo que el estado debe persistirse fuera del
runner. Si se versiona `data/oposiciones.db`, el workflow debe:

- usar `concurrency` con `cancel-in-progress: false`;
- evitar dos escritores simultáneos;
- cerrar la conexión y consolidar cualquier WAL antes del commit;
- hacer commit solo si el estado persistente cambió;
- actualizarse antes de escribir y resolver/reintentar un push concurrente;
- disponer de `permissions: contents: write`;
- ejecutar también mediante `workflow_dispatch`;
- no registrar secretos.

Limitaciones aceptadas del SQLite versionado:

- crecimiento binario del historial Git;
- conflictos si se elimina la serialización;
- dependencia de permisos de escritura del repositorio;
- riesgo de repetir o perder un aviso si se envía Telegram pero luego no puede
  persistirse el commit remoto.

La migración a una base externa es la opción recomendada cuando aumente el uso.

Los cron de GitHub usan UTC y pueden retrasarse. Las horas son orientativas; no
se promete ejecución exacta al minuto.

## 18. Configuración y observabilidad

La configuración debe permitir:

- habilitar/deshabilitar fuentes;
- municipios y radio;
- titulaciones admitidas;
- ventana retrospectiva y solapamiento;
- timeout, tamaño máximo de documento y User-Agent;
- umbral de avisos por fallo de fuente;
- Telegram y `dry-run`;
- días de recordatorio.

Los logs incluyen por ejecución:

- fuente, duración y estado;
- elementos leídos, candidatos incluidos y descartados;
- nuevas, actualizaciones, revisiones y recordatorios;
- outbox pendiente, incierta o fallida;
- errores sin secretos.

Una fuente que falle repetidamente puede generar una única alerta de salud con
periodo de enfriamiento. No se envía una alerta idéntica en cada ejecución.

## 19. Criterios de aceptación de la v1

### Instalación y seguridad

- [ ] Instalación limpia en Python 3.12.
- [ ] Configuración de ejemplo válida y documentada.
- [ ] Ningún token o chat ID versionado o impreso.
- [ ] Ejecución `dry-run` sin credenciales reales.
- [ ] Base SQLite creada mediante esquema versionado.

### Fuentes

- [ ] Cada fuente marcada activa consulta una URL oficial real.
- [ ] Cada parser tiene al menos un fixture reproducible.
- [ ] Una fuente caída no impide procesar las restantes.
- [ ] Se distingue `0 resultados` de `FAILED` o `DEGRADED`.
- [ ] Se respetan límites de acceso, timeout, tamaño y `robots.txt`.
- [ ] BOE, BOJA y BOP Jaén completan una prueba de humo.
- [ ] IAAP y cada fuente local activa informan claramente su salud.
- [ ] PAG permanece desactivado salvo configuración compatible con sus reglas.

### Filtros

- [ ] «Bachiller o Técnico» produce `COMPATIBLE`.
- [ ] «Técnico Superior de la familia Informática» produce `COMPATIBLE`.
- [ ] Universidad mencionada solo en méritos o temario no produce exclusión.
- [ ] Universidad como requisito único obligatorio produce `NO_COMPATIBLE`.
- [ ] Requisito remitido a otra norma o PDF ilegible produce `REVISAR`.
- [ ] Promoción interna exclusiva se excluye.
- [ ] Acceso mixto con plazas libres se conserva.
- [ ] TAI nacional sin destino supera el filtro geográfico.
- [ ] Junta informática sin provincia supera el filtro geográfico.

### Identidad y cambios

- [ ] Misma publicación repetida genera cero eventos nuevos.
- [ ] Misma convocatoria con URL distinta genera cero avisos nuevos.
- [ ] Bases en BOP y apertura en BOE forman un expediente y una actualización.
- [ ] Cambio solo de formato o navegación no genera actualización.
- [ ] Cambio de plazo o examen genera exactamente un evento nuevo.
- [ ] Dos convocatorias similares de años o resoluciones distintas no se unen.
- [ ] Una publicación con dos categorías puede producir dos expedientes.
- [ ] Reiniciar el proceso conserva identidad, referencias y deduplicación.
- [ ] La desaparición de una lista no marca el expediente como finalizado.

### Notificaciones y recordatorios

- [ ] El evento se persiste antes del intento de envío.
- [ ] Un evento `SENT` nunca se reclama de nuevo.
- [ ] Un resultado remoto ambiguo queda `UNCERTAIN` y no se reintenta solo.
- [ ] Un 429 confirmado se reintenta respetando `Retry-After`.
- [ ] La primera ejecución no inunda Telegram con procesos históricos.
- [ ] Solo plazos confirmados generan recordatorios.
- [ ] Un cambio de fecha crea recordatorios para la nueva fecha sin repetir la
      anterior.

### Automatización

- [ ] Workflow manual funcional.
- [ ] Workflow programado con una sola ejecución concurrente.
- [ ] Persistencia del estado verificada tras dos runners distintos.
- [ ] Un fallo de push deja un error visible y no se declara éxito completo.
- [ ] README diferencia fuentes operativas, degradadas y desactivadas.

La v1 se considera terminada cuando todos los puntos obligatorios anteriores
están verificados por tests o una prueba de humo documentada. La mera existencia
de un archivo de adaptador no cuenta como cobertura.

## 20. Hoja de ruta

### Fase 0 — Núcleo reproducible

- configuración validada;
- modelo y migración SQLite;
- normalización y SHA-256 estable;
- clasificadores con evidencia;
- outbox `at_most_once`;
- Telegram `dry-run`;
- fixtures y tests unitarios.

### Fase 1 — Fuentes oficiales estructuradas

- BOE;
- BOJA;
- BOP Jaén;
- referencias cruzadas y descarga controlada de documentos;
- política de primera ejecución.

### Fase 2 — Seguimiento y ámbito local

- IAAP;
- Martos, Torredonjimeno, Torredelcampo y Jaén;
- Diputación y Universidad de Jaén;
- alertas de salud de fuentes;
- recordatorios;
- GitHub Actions con persistencia serializada.

### Fase 3 — Robustez operativa

- mejor extracción de secciones PDF;
- relaciones explícitas entre resoluciones;
- cola de posibles duplicados;
- métricas y comprobaciones de silencio anómalo;
- base externa opcional;
- más municipios configurables.

### Fuera de v1

- OCR de documentos escaneados;
- panel web;
- clasificación asistida por modelos externos;
- resolución jurídica automática de equivalencias;
- scraping de áreas protegidas;
- garantía de entrega exactamente una vez.

## 21. Regla de cierre

El bot debe ser conservador al descartar y estricto al notificar:

> Si no puede demostrar incompatibilidad, marca `REVISAR`. Si no puede demostrar
> un cambio material, no vuelve a avisar.

Toda limitación de acceso o cobertura debe quedar visible en logs y documentación.
El objetivo de la v1 es una vigilancia útil, trazable y honesta, no una falsa
promesa de exhaustividad.
