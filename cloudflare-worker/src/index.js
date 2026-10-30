const TELEGRAM_API = "https://api.telegram.org";
const GITHUB_API = "https://api.github.com";
const BOP_ORIGIN = "https://bop.dipujaen.es";
const BOP_DAY_MAX_BYTES = 2_000_000;
const BOP_DOCUMENT_MAX_BYTES = 15_000_000;

function textResponse(text, status = 200) {
  return new Response(text, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

function required(env, name) {
  const value = String(env[name] || "").trim();
  if (!value) throw new Error(`Falta la variable ${name}`);
  return value;
}

function bopProxySecret(env) {
  // Prefer a dedicated secret; the Telegram token remains a fallback so an
  // existing deployment keeps working until BOP_PROXY_TOKEN is configured.
  return String(env.BOP_PROXY_TOKEN || env.TELEGRAM_BOT_TOKEN || "").trim();
}

// Constant-time comparison so response timing does not leak a secret.
function safeEqual(received, expected) {
  const encoder = new TextEncoder();
  const a = encoder.encode(String(received || ""));
  const b = encoder.encode(String(expected || ""));
  let diff = a.length ^ b.length;
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    diff |= (a[index] || 0) ^ (b[index] || 0);
  }
  return diff === 0 && b.length > 0;
}

// Ignore commands older than this: Telegram redelivers pending updates after
// an outage and an old /buscar must not start a search hours later.
const MAX_MESSAGE_AGE_SECONDS = 600;
const ACTIVE_RUN_STATES = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

function parseBopDate(value) {
  const match = /^(\d{2})-(\d{2})-(\d{4})$/.exec(value);
  if (!match) return null;
  const [, dayText, monthText, yearText] = match;
  const day = Number(dayText);
  const month = Number(monthText);
  const year = Number(yearText);
  const instant = Date.UTC(year, month - 1, day);
  const parsed = new Date(instant);
  if (
    parsed.getUTCFullYear() !== year
    || parsed.getUTCMonth() !== month - 1
    || parsed.getUTCDate() !== day
  ) return null;

  // The collector only asks for the current incremental window.  Limiting the
  // range prevents this private endpoint from becoming a general BOP mirror.
  const ageDays = Math.floor((Date.now() - instant) / 86_400_000);
  if (ageDays < -2 || ageDays > 45) return null;
  return {
    pathDate: value,
    isoDate: `${yearText}-${monthText}-${dayText}`,
    ageDays,
  };
}

function bopResponse(body, status, contentType = "text/plain; charset=utf-8") {
  return new Response(body, {
    status,
    headers: {
      "content-type": contentType,
      "cache-control": "private, max-age=0, no-store",
      "x-bop-proxy": "1",
      "x-content-type-options": "nosniff",
    },
  });
}

function bopDependencyError(code) {
  return bopResponse(`BOP_DEPENDENCY_${code}`, 424);
}

function validUpstream(response, expectedType, maxBytes) {
  if (response.status >= 300 && response.status < 400) return false;
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (!contentType.includes(expectedType)) return false;
  const contentLength = Number(response.headers.get("content-length") || 0);
  return !contentLength || contentLength <= maxBytes;
}

function bopDayLinks(html, day) {
  const links = [];
  const hrefs = [...html.matchAll(/<a\b[^>]*\bhref\s*=\s*(["'])(.*?)\1/giu)]
    .map(match => match[2].replaceAll("&amp;", "&"));
  for (const href of hrefs) {
    let candidate;
    try { candidate = new URL(href, BOP_ORIGIN); } catch { continue; }
    if (
      candidate.origin === BOP_ORIGIN
      && candidate.pathname === "/descargarws.dip"
      && candidate.searchParams.get("fechaBoletin") === day.isoDate
      && /^\d{1,10}$/.test(candidate.searchParams.get("numeroEdicto") || "")
    ) links.push(candidate);
  }
  return links;
}

function validEmptyBopDay(html, day) {
  const normalized = html.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
  return normalized.includes(`no hay ningun boletin publicado para el dia ${day.pathDate}`);
}

async function fetchBopDay(day) {
  return fetch(`${BOP_ORIGIN}/bop/${day.pathDate}`, {
    method: "GET",
    headers: {
      accept: "text/html,application/xhtml+xml",
      "accept-language": "es-ES,es;q=0.9",
    },
    redirect: "manual",
    cf: { cacheEverything: true, cacheTtl: day.ageDays > 0 ? 86400 : 300 },
  });
}

async function handleBopProxy(request, env, url) {
  if (request.method !== "GET") return bopResponse("method not allowed", 405);
  const secret = bopProxySecret(env);
  const authorization = request.headers.get("authorization") || "";
  if (!secret || !safeEqual(authorization, `Bearer ${secret}`)) return bopResponse("unauthorized", 401);

  const dayMatch = /^\/bop\/day\/(\d{2}-\d{2}-\d{4})$/.exec(url.pathname);
  const edictMatch = /^\/bop\/edict\/(\d{2}-\d{2}-\d{4})\/(\d{1,10})$/.exec(url.pathname);
  if (!dayMatch && !edictMatch) return bopResponse("not found", 404);

  const day = parseBopDate((dayMatch || edictMatch)[1]);
  if (!day) return bopResponse("invalid or out-of-range date", 400);

  let dayResponse;
  try {
    dayResponse = await fetchBopDay(day);
  } catch (error) {
    console.error("BOP day fetch failed", error);
    return bopResponse("BOP upstream unavailable", 504);
  }
  if (dayResponse.status === 404) {
    // A missing bulletin is a valid, covered day (weekends and holidays).
    return bopResponse(null, 204);
  }
  if (!dayResponse.ok || !validUpstream(dayResponse, "text/html", BOP_DAY_MAX_BYTES)) {
    console.warn("BOP day rejected", {
      status: dayResponse.status,
      contentType: dayResponse.headers.get("content-type"),
      contentLength: dayResponse.headers.get("content-length"),
    });
    if (!dayResponse.ok) return bopDependencyError(`STATUS_${dayResponse.status}`);
    const contentType = String(dayResponse.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("text/html")) return bopDependencyError("DAY_CONTENT_TYPE");
    return bopDependencyError("DAY_CONTENT_LENGTH");
  }

  const dayBytes = await dayResponse.arrayBuffer();
  if (dayBytes.byteLength > BOP_DAY_MAX_BYTES) {
    return bopDependencyError("DAY_TOO_LARGE");
  }
  const html = new TextDecoder("windows-1252").decode(dayBytes);
  const officialLinks = bopDayLinks(html, day);
  if (!officialLinks.length && !validEmptyBopDay(html, day)) {
    console.warn("BOP day HTML not recognized", {
      requestedDate: day.isoDate,
      bytes: dayBytes.byteLength,
    });
    return bopDependencyError("DAY_UNRECOGNIZED");
  }

  if (dayMatch) {
    return bopResponse(
      dayBytes,
      200,
      dayResponse.headers.get("content-type") || "text/html; charset=iso-8859-1",
    );
  }

  const edict = edictMatch[2];
  const documentUrl = officialLinks.find(
    candidate => candidate.searchParams.get("numeroEdicto") === edict,
  );
  if (!documentUrl) return bopResponse("edict not found in bulletin", 404);

  let documentResponse;
  try {
    documentResponse = await fetch(documentUrl.toString(), {
      method: "GET",
      headers: {
        accept: "application/pdf",
        "accept-language": "es-ES,es;q=0.9",
      },
      redirect: "manual",
      cf: { cacheEverything: true, cacheTtl: day.ageDays > 0 ? 86400 : 300 },
    });
  } catch (error) {
    console.error("BOP document fetch failed", error);
    return bopResponse("BOP document unavailable", 504);
  }
  if (!documentResponse.ok || !validUpstream(documentResponse, "application/pdf", BOP_DOCUMENT_MAX_BYTES)) {
    if (!documentResponse.ok) return bopDependencyError(`DOCUMENT_STATUS_${documentResponse.status}`);
    const contentType = String(documentResponse.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("application/pdf")) return bopDependencyError("DOCUMENT_CONTENT_TYPE");
    return bopDependencyError("DOCUMENT_CONTENT_LENGTH");
  }
  return bopResponse(documentResponse.body, 200, "application/pdf");
}

async function telegram(env, method, payload) {
  const token = required(env, "TELEGRAM_BOT_TOKEN");
  const response = await fetch(`${TELEGRAM_API}/bot${token}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok || !data.ok) {
    // Never include the request URL: it contains the bot token.
    throw new Error(`Telegram rechazo ${method}: ${data.description || `HTTP ${response.status}`}`);
  }
  return data.result;
}

async function reply(env, chatId, text) {
  if (text.length > 3600) {
    const chunks = text.match(/[\s\S]{1,3500}/gu) || [];
    for (const chunk of chunks) await reply(env, chatId, chunk);
    return;
  }
  return telegram(env, "sendMessage", {
    chat_id: chatId,
    text,
    disable_web_page_preview: true,
  });
}

async function configureTelegram(request, env) {
  const webhookSecret = required(env, "WEBHOOK_SECRET");
  const origin = new URL(request.url).origin;

  await telegram(env, "setWebhook", {
    url: `${origin}/telegram`,
    secret_token: webhookSecret,
    // Editing an old "/buscar" must not start another search.
    allowed_updates: ["message"],
  });
  await telegram(env, "setMyCommands", {
    commands: [
      { command: "buscar", description: "Iniciar una búsqueda ahora" },
      { command: "estado", description: "Consultar la última ejecución" },
      { command: "convocatorias", description: "Oportunidades y procesos por revisar" },
      { command: "detalle", description: "Ficha completa: /detalle ID" },
      { command: "seguir", description: "Activar avisos: /seguir ID" },
      { command: "dejar", description: "Desactivar avisos: /dejar ID" },
      { command: "seguimientos", description: "Ver procesos que sigues" },
      { command: "ayuda", description: "Mostrar la ayuda" },
    ],
  });
}

function githubHeaders(env, extra = {}) {
  return {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${required(env, "GITHUB_TOKEN")}`,
    "user-agent": "oposiciones-telegram-worker/1.0",
    "x-github-api-version": "2026-03-10",
    ...extra,
  };
}

function repoPath(env) {
  const owner = encodeURIComponent(String(env.GITHUB_OWNER || "Alarbon").trim());
  const repo = encodeURIComponent(String(env.GITHUB_REPO || "bot_opos").trim());
  return `${GITHUB_API}/repos/${owner}/${repo}`;
}

function githubFailure(status) {
  if (status === 401 || status === 403) return "el token de GitHub no tiene permiso o ha caducado";
  if (status === 404) return "no se encuentra el repositorio o el workflow";
  if (status === 422) return "GitHub rechazó los parámetros (rama o entradas del workflow)";
  if (status === 429) return "límite de peticiones de GitHub alcanzado";
  if (status >= 500) return "GitHub no está disponible ahora mismo";
  return `respuesta inesperada de GitHub (HTTP ${status})`;
}

async function workflowRuns(env, perPage = 20) {
  const workflow = encodeURIComponent(String(env.GITHUB_WORKFLOW || "oposiciones.yml").trim());
  const response = await fetch(`${repoPath(env)}/actions/workflows/${workflow}/runs?per_page=${perPage}`, {
    headers: githubHeaders(env),
  });
  if (!response.ok) throw new Error(`No pude consultar GitHub: ${githubFailure(response.status)}.`);
  const data = await response.json();
  return Array.isArray(data.workflow_runs) ? data.workflow_runs : [];
}

async function dispatchSearch(env, operation = null, processId = null) {
  const workflow = operation ? "seguimiento.yml" : String(env.GITHUB_WORKFLOW || "oposiciones.yml").trim();
  const ref = String(env.GITHUB_REF || "main").trim();
  const url = `${repoPath(env)}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: githubHeaders(env, { "content-type": "application/json" }),
      body: JSON.stringify({ ref, inputs: operation ? { operation, process_id: processId } : { dry_run: false } }),
    });
  } catch {
    throw new Error("No hubo respuesta de GitHub (fallo de red). No sé si la solicitud llegó: revisa /estado antes de repetirla.");
  }
  if (response.status !== 200 && response.status !== 204) {
    throw new Error(`GitHub no aceptó la solicitud: ${githubFailure(response.status)}. No se ha iniciado nada.`);
  }
  if (response.status !== 200) return null;
  try {
    const data = await response.json();
    return data.html_url || (data.workflow_run_id ? `https://github.com/${env.GITHUB_OWNER || "Alarbon"}/${env.GITHUB_REPO || "bot_opos"}/actions/runs/${data.workflow_run_id}` : null);
  } catch { return null; }
}

async function catalog(env) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "Alarbon");
  const repo = encodeURIComponent(env.GITHUB_REPO || "bot_opos");
  const ref = encodeURIComponent(env.GITHUB_REF || "main");
  const response = await fetch(`https://raw.githubusercontent.com/${owner}/${repo}/${ref}/data/catalog.json?fresh=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Catálogo todavía no disponible. Prueba /buscar y espera a que termine.");
  const data = await response.json();
  if (!Array.isArray(data.processes)) throw new Error("Catálogo inválido");
  return data;
}

function findProcess(data, id) {
  if (!/^[a-f0-9-]{8,36}$/i.test(id)) throw new Error("Usa /detalle ID con el ID que aparece en /convocatorias (mínimo 8 caracteres).");
  const matches = data.processes.filter(p => [p.id, ...(p.aliases || [])].some(alias => alias.startsWith(id.toLowerCase())));
  if (matches.length !== 1) throw new Error("ID no encontrado o ambiguo; consulta /convocatorias.");
  return matches[0];
}

function listProcesses(data, followedOnly = false, page = 1) {
  const items = data.processes.filter(p => !followedOnly || p.followed);
  const pages = Math.max(1, Math.ceil(items.length / 10));
  if (!Number.isInteger(page) || page < 1 || page > pages) throw new Error(`Página inválida. Hay ${pages} página(s).`);
  const labels = { OPEN: "✅ Plazo y perfil confirmados", REVIEW: "🔎 Por revisar; inscripción no confirmada", TRACKING: "📌 Proceso avanzado o cerrado; no es una nueva inscripción" };
  return [followedOnly ? "📌 Tus seguimientos" : "📋 Convocatorias informáticas", `Datos consultados: ${data.generated_at}`, `Página ${page}/${pages}. Actualiza con /buscar.`, "",
    ...(items.length ? items.slice((page - 1) * 10, page * 10).map(p => `${p.id.slice(0, 8)} — ${p.title}\n${labels[p.category] || "Por revisar"}${p.followed ? " · Siguiendo" : ""}\n${p.organisation}\nMunicipio: ${p.locality || "No confirmado"}\nProvincia: ${p.province || "No confirmada"}\nÁmbito: ${p.scope || "No confirmado"}\n/detalle ${p.id.slice(0, 8)}`) : ["No hay procesos registrados en esta lista."]),
    "", `Más páginas: /${followedOnly ? "seguimientos" : "convocatorias"} N`, "Para recibir cambios: /seguir ID. Para quitar: /dejar ID.",
    ...(data.sources?.filter(s => s.status === "PARTIAL").map(s => `⚠️ ${s.error || `${s.source}: cobertura oficial pendiente de confirmar`}`) || []),
    ...(data.sources?.some(s => s.status === "ERROR") ? ["⚠️ Algunas fuentes fallaron en la última consulta; cobertura incompleta."] : []),
  ].join("\n\n");
}

function processDetail(p, data) {
  const value = v => v === null || v === undefined || v === "" ? "No confirmado" : String(v);
  const labels = { OPEN: "✅ Oportunidad con plazo y perfil confirmados", REVIEW: "🔎 POR REVISAR — no confirma que puedas inscribirte", TRACKING: "📌 PROCESO AVANZADO O CERRADO — no es una nueva inscripción" };
  const links = [...(p.links || [])];
  if (p.url && !links.some(l => l.url === p.url)) links.unshift({ label: "Fuente principal", url: p.url });
  return [labels[p.category], `ID: ${p.id.slice(0, 8)}`, `Puesto: ${p.title}`, `Organismo: ${p.organisation}`, `Municipio: ${value(p.locality)}`, `Provincia: ${value(p.province)}`, `Ámbito: ${value(p.scope)}`, "Destino concreto: comprobar en las bases; el ámbito no garantiza destino.",
    `Grupo: ${value(p.group)}`, `Plazas: ${value(p.positions)}`, `Acceso: ${value(p.access)}`, `Titulación: ${value(p.qualification_text)}`, `Compatibilidad: ${value(p.compatibility)}`,
    ...(p.review_reason ? [`Pendiente: ${p.review_reason}`] : []), `Estado: ${p.status}`, `Publicación: ${value(p.publication_date)}`, `Fin de solicitudes: ${value(p.deadline)} (${p.deadline_confirmed ? "fecha confirmada" : "sin confirmar"})`,
    `Examen: ${value(p.exam_date)} · Hora: ${value(p.exam_time)} · Lugar: ${value(p.exam_place)}`, `Resumen: ${value(p.summary)}`,
    `Última consulta: ${p.last_seen}`, `Último cambio: ${p.last_changed}`, `Catálogo: ${data.generated_at}`,
    "Historial detectado:", ...(p.history?.length ? p.history.map(h => `${h.date}: ${h.kind} ${h.changes.join(", ")}`) : ["Sin cambios adicionales registrados."]),
    "Enlaces oficiales:", ...links.filter(l => /^https?:\/\//.test(l.url)).map(l => `${l.label || "Documento"}\n${l.url}`),
    `Seguimiento: ${p.followed ? "ACTIVO" : "NO ACTIVO"}`, `/seguir ${p.id.slice(0, 8)} · /dejar ${p.id.slice(0, 8)}`, "Seguir no te inscribe. Confirma los requisitos exactos y el plazo en las bases oficiales.",
  ].join("\n\n");
}

const RUN_STATES = {
  success: "✅ correcta", failure: "❌ fallida", in_progress: "⏳ en curso", queued: "⏳ en cola",
  cancelled: "cancelada", waiting: "esperando", pending: "pendiente", requested: "solicitada",
  timed_out: "tiempo agotado", startup_failure: "❌ no pudo arrancar", skipped: "omitida",
};

function localDate(value) {
  return value
    ? new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", dateStyle: "short", timeStyle: "medium" }).format(new Date(value))
    : "No registrada";
}

function runOrigin(run) {
  if (run.event === "schedule" || run.display_title === "Busqueda automatica programada") return "automática programada";
  if (run.event === "workflow_dispatch") return "manual";
  return run.event || "desconocido";
}

function runState(run) {
  const state = run.status === "completed" ? run.conclusion : run.status;
  return RUN_STATES[state] || state || "desconocido";
}

function reportLines(report) {
  const lines = [];
  const collection = report.collection;
  if (!collection) {
    lines.push("No se completó la consulta de fuentes en esa ejecución.");
  } else {
    const failed = Object.entries(collection.errors || {});
    const delayed = Object.entries(collection.warnings || {});
    const attempted = Number(collection.sources_attempted || 0);
    if (attempted && failed.length >= attempted) {
      lines.push("❌ No se pudo consultar ninguna fuente: eso NO significa que no haya novedades.");
    }
    lines.push(
      `Fuentes: ${attempted - failed.length - delayed.length} al día / ${delayed.length} con aviso de cobertura / ${failed.length} fallidas.`,
      `Registros examinados: ${collection.fetched ?? "?"}; nuevos procesos: ${collection.created ?? "?"}; actualizados: ${collection.updated ?? "?"}.`,
    );
    if (collection.compatible !== undefined) {
      lines.push(`Clasificación: ${collection.compatible} compatibles, ${collection.review} por revisar.`);
    }
    if (collection.silent_new) lines.push(`${collection.silent_new} nuevo(s) sin aviso individual (plazo sin confirmar): mira /convocatorias.`);
    if (delayed.length) lines.push("⚠️ Avisos de cobertura: " + delayed.map(([name, detail]) => `${name}: ${detail}`).join("; "));
    if (failed.length) lines.push("⚠️ Cobertura incompleta. Fuentes fallidas: " + failed.map(([name]) => name).join(", "));
  }
  if (report.delivery) {
    lines.push(`Avisos enviados: ${report.delivery.sent}; reintentos pendientes: ${report.delivery.retryable}; entregas inciertas: ${report.delivery.uncertain}.`);
  } else {
    lines.push("Envío de avisos no registrado en esa ejecución.");
  }
  return lines;
}

async function latestStatus(env) {
  const runs = await workflowRuns(env, 20);
  if (!runs.length) return "Todavía no hay ejecuciones registradas.";
  const newest = runs[0];
  const active = runs.filter(run => ACTIVE_RUN_STATES.has(run.status));
  const started = runs.find(run => run.run_started_at && run.status !== "queued");
  const completed = runs.find(run => run.status === "completed");
  const succeeded = runs.find(run => run.status === "completed" && run.conclusion === "success");
  const lines = [
    "Estado de las búsquedas (hora peninsular):",
    `• Última solicitada: ${localDate(newest.created_at)} — ${runState(newest)} (${runOrigin(newest)})`,
    `• Última iniciada: ${started ? localDate(started.run_started_at) : "no disponible"}`,
    `• Última completada: ${completed ? `${localDate(completed.updated_at)} — ${runState(completed)} (${runOrigin(completed)})` : "ninguna en las 20 últimas"}`,
    `• Última correcta: ${succeeded ? localDate(succeeded.updated_at) : "ninguna en las 20 últimas"}`,
    active.length
      ? `• Búsqueda en marcha: sí (${runState(active[0])}, solicitada ${localDate(active[0].created_at)})`
      : "• Búsqueda en marcha: no",
    "",
  ];
  if (completed) {
    lines.push(`Resultado de la última completada (inicio ${localDate(completed.run_started_at || completed.created_at)}):`);
    try {
      const summaryResponse = await fetch(`https://raw.githubusercontent.com/${encodeURIComponent(env.GITHUB_OWNER || "Alarbon")}/${encodeURIComponent(env.GITHUB_REPO || "bot_opos")}/${encodeURIComponent(env.GITHUB_REF || "main")}/data/latest_run.json?fresh=${Date.now()}`, { cache: "no-store" });
      if (summaryResponse.ok) {
        const report = await summaryResponse.json();
        if (String(report.run_id) === String(completed.id)) {
          lines.push(...reportLines(report));
        } else {
          lines.push("Resumen detallado de esa ejecución no disponible (puede no haberse guardado o la caché de GitHub aún no lo muestra); no se muestran estadísticas antiguas.");
        }
      } else lines.push("Resumen detallado no disponible.");
    } catch { lines.push("No se pudo cargar el resumen detallado."); }
    lines.push(completed.html_url);
  }
  if (active.length && active[0].html_url) lines.push("", `En curso: ${active[0].html_url}`);
  lines.push("", "Para iniciar una búsqueda: /buscar");
  return lines.join("\n");
}

function commandFrom(message) {
  const first = String(message || "").trim().split(/\s+/, 1)[0].toLowerCase();
  return first.split("@", 1)[0];
}

async function handleUpdate(update, env, nowSeconds = Math.floor(Date.now() / 1000)) {
  // Edited messages are ignored on purpose: editing an old "/buscar" must not
  // trigger a new search.
  const message = update?.message;
  if (!message?.text || message?.chat?.id === undefined) return;

  const chatId = String(message.chat.id);
  const allowedChatId = required(env, "TELEGRAM_CHAT_ID");
  if (chatId !== allowedChatId) return;
  if (Number.isFinite(message.date) && nowSeconds - message.date > MAX_MESSAGE_AGE_SECONDS) {
    console.warn("stale command ignored", { ageSeconds: nowSeconds - message.date });
    return;
  }

  const command = commandFrom(message.text);
  const argument = message.text.trim().split(/\s+/)[1] || "";
  if (["/convocatorias", "/seguimientos", "/detalle", "/seguir", "/dejar"].includes(command) || (command === "/buscar" && argument)) {
    try {
      const data = await catalog(env);
      if (command === "/convocatorias" || command === "/seguimientos") {
        await reply(env, chatId, listProcesses(data, command === "/seguimientos", argument ? Number(argument) : 1));
      } else {
        if (!argument) {
          await reply(env, chatId, `Usa ${command} ID con uno de estos procesos:\n\n${listProcesses(data)}`);
          return;
        }
        const process = findProcess(data, argument);
        if (command === "/seguir" || command === "/dejar") {
          const runUrl = await dispatchSearch(env, command === "/seguir" ? "follow" : "unfollow", process.id);
          await reply(env, chatId, `⏳ Cambio de seguimiento solicitado para ${process.id.slice(0, 8)}. Se confirmará cuando GitHub lo guarde; todavía no está confirmado.\n${runUrl || "Consulta /seguimientos en unos minutos."}`);
        } else await reply(env, chatId, processDetail(process, data));
      }
    } catch (error) { await reply(env, chatId, `❌ ${error.message || error}`); }
    return;
  }
  if (command === "/buscar") {
    let active = null;
    try {
      active = (await workflowRuns(env, 10)).find(run => ACTIVE_RUN_STATES.has(run.status)) || null;
    } catch (error) {
      // Not knowing is not a reason to refuse: GitHub's concurrency group
      // still serialises the runs.
      console.warn("active run check failed", String(error.message || error));
    }
    if (active) {
      await reply(env, chatId, [
        `⏳ Ya hay una búsqueda ${runState(active)} (solicitada ${localDate(active.created_at)}).`,
        "No he solicitado otra para no duplicar el trabajo. Recibirás el resumen cuando termine; mientras tanto, /estado.",
        active.html_url || "",
      ].join("\n"));
      return;
    }
    try {
      const runUrl = await dispatchSearch(env);
      await reply(env, chatId, [
        "📨 Búsqueda solicitada a GitHub Actions.",
        "Todavía no ha terminado (puede tardar unos segundos en empezar). Cuando acabe recibirás un resumen, también si no hay novedades.",
        "Consulta /estado para ver si está en marcha.",
        runUrl || "",
      ].join("\n"));
    } catch (error) {
      await reply(env, chatId, `❌ No se ha podido solicitar la búsqueda. ${String(error.message || error)}`);
    }
    return;
  }

  if (command === "/estado") {
    try {
      await reply(env, chatId, `ℹ️ ${await latestStatus(env)}`);
    } catch (error) {
      await reply(env, chatId, `❌ No pude consultar el estado: ${String(error.message || error)}`);
    }
    return;
  }

  if (command === "/start" || command === "/ayuda") {
    // Refresh the menu after deployments without changing the existing webhook.
    await telegram(env, "setMyCommands", { commands: [
      { command: "buscar", description: "Buscar novedades o /buscar ID" },
      { command: "convocatorias", description: "Ver oportunidades y procesos por revisar" },
      { command: "detalle", description: "Ficha e historial: /detalle ID" },
      { command: "seguir", description: "Activar seguimiento: /seguir ID" },
      { command: "dejar", description: "Desactivar seguimiento: /dejar ID" },
      { command: "seguimientos", description: "Ver seguimientos activos" },
      { command: "estado", description: "Consultar ejecución de la búsqueda" },
      { command: "ayuda", description: "Ayuda y comandos" },
    ] }).catch(error => console.error("command menu failed", error));
    await reply(
      env,
      chatId,
      [
        "🤖 Bot de oposiciones de informática",
        "",
        "/buscar — iniciar una búsqueda ahora",
        "/estado — consultar la última ejecución",
        "/convocatorias — oportunidades y procesos por revisar",
        "/detalle ID — información, bases e historial",
        "/buscar ID — consultar la misma ficha sin iniciar otra búsqueda",
        "/seguir ID — activar avisos de cambios",
        "/dejar ID — desactivar seguimiento",
        "/seguimientos — consultar lo que sigues",
        "/ayuda — mostrar esta ayuda",
        "",
        "Las búsquedas se inician con /buscar, desde GitHub o, si la búsqueda diaria está activada en el repositorio, una vez cada mañana. Los cambios y recordatorios de seguimientos se comprueban en cada búsqueda.",
        "/buscar solo solicita la búsqueda: el resultado llega en un resumen al terminar.",
        "Seguir una convocatoria no te inscribe. Los datos por revisar no confirman elegibilidad.",
      ].join("\n"),
    );
  }
}

export default {
  async fetch(request, env, context) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return textResponse("ok");
    }

    if (url.pathname.startsWith("/bop/")) {
      return handleBopProxy(request, env, url);
    }

    if (request.method === "POST" && url.pathname === "/admin/configure") {
      const expectedSecret = required(env, "WEBHOOK_SECRET");
      const receivedSecret = request.headers.get("authorization") || "";
      if (!safeEqual(receivedSecret, `Bearer ${expectedSecret}`)) return textResponse("forbidden", 403);

      try {
        await configureTelegram(request, env);
        return textResponse("configured");
      } catch (error) {
        console.error("configuration failed", error);
        return textResponse("configuration failed", 502);
      }
    }

    if (url.pathname !== "/telegram") return textResponse("not found", 404);
    if (request.method !== "POST") return textResponse("method not allowed", 405);

    const expectedSecret = required(env, "WEBHOOK_SECRET");
    const receivedSecret = request.headers.get("x-telegram-bot-api-secret-token") || "";
    if (!safeEqual(receivedSecret, expectedSecret)) return textResponse("forbidden", 403);

    let update;
    try {
      update = await request.json();
    } catch {
      return textResponse("invalid json", 400);
    }

    context.waitUntil(
      handleUpdate(update, env).catch((error) => console.error("update failed", error)),
    );
    return textResponse("ok");
  },
};
