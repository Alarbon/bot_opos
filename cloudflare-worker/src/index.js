async function boundedFetch(url, options = {}) {
  return fetch(url, { ...options, signal: AbortSignal.timeout(15_000) });
}
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
    documentResponse = await boundedFetch(documentUrl.toString(), {
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
  let response;
  try {
    response = await boundedFetch(`${TELEGRAM_API}/bot${token}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // A platform error may include the URL (and therefore the bot token).
    throw new Error("Telegram: respuesta no confirmada; conexión o tiempo de espera agotado.");
  }
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok || !data.ok) {
    // Never include the request URL: it contains the bot token.
    throw new Error(`Telegram rechazo ${method}: ${data.description || `HTTP ${response.status}`}`);
  }
  return data.result;
}

async function reply(env, chatId, text, markup = undefined, messageId = undefined, html = false) {
  if (text.length > 3900) {
    const chunks = text.match(/[\s\S]{1,3500}/gu) || [];
    for (const chunk of chunks) await reply(env, chatId, chunk);
    return;
  }
  return telegram(env, messageId ? "editMessageText" : "sendMessage", {
    chat_id: chatId,
    ...(messageId ? { message_id: messageId } : {}),
    ...(markup ? { reply_markup: markup } : {}),
    ...(html ? { parse_mode: "HTML" } : {}),
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
    allowed_updates: ["message", "callback_query"],
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
  const response = await boundedFetch(`${repoPath(env)}/actions/workflows/${workflow}/runs?per_page=${perPage}`, {
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
    response = await boundedFetch(url, {
      method: "POST",
      headers: githubHeaders(env, { "content-type": "application/json" }),
      body: JSON.stringify({ ref, inputs: operation ? { operation, process_id: processId } : { dry_run: false } }),
    });
  } catch {
    const error = new Error("No hubo respuesta de GitHub (fallo de red). No sé si la solicitud llegó: revisa /estado antes de repetirla.");
    error.uncertain = true;
    throw error;
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
  const response = await boundedFetch(`https://raw.githubusercontent.com/${owner}/${repo}/${ref}/data/catalog.json?fresh=${Date.now()}`, { cache: "no-store" });
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

const PAGE_SIZE = 5;
const LABELS = { OPEN: "✅ Abierta · perfil confirmado", REVIEW: "🔎 Requiere revisión", TRACKING: "📌 Seguimiento · proceso avanzado o cerrado" };
const escapeHTML = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const short = (value, size = 180) => {
  const chars = Array.from(String(value ?? ""));
  return chars.length > size ? chars.slice(0, size).join("") + "…" : chars.join("");
};
function clean(value, size = 180) {
  let result = "";
  for (const char of String(value || "No confirmado")) {
    const escaped = escapeHTML(char);
    if (result.length + escaped.length > size - 1) return result + "…";
    result += escaped;
  }
  return result;
}
const button = (text, data) => ({ text, callback_data: data });
const menu = () => ({ inline_keyboard: [[button("📋 Convocatorias", "l:a:1"), button("⭐ Seguimientos", "l:f:1")], [button("🔄 Buscar novedades", "m:buscar"), button("ℹ️ Estado", "m:estado")]] });
function safeURL(value) {
  if (String(value || "").length > 500) return null;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}
function dateOnly(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? value.split("-").reverse().join("/") : "No confirmada";
}
function processActions(p, mode = "a", page = 1) {
  const rows = [[button(p.followed ? "🔕 Dejar de seguir" : "⭐ Seguir", `${p.followed ? "u" : "f"}:${p.id}:${mode}:${page}`)]];
  const url = safeURL(p.url);
  if (url) rows.push([{ text: "🔗 Fuente oficial", url }]);
  rows.push([{ text: "📋 Copiar comando completo", copy_text: { text: `/detalle ${p.id}` } }]);
  rows.push([button("↩️ Volver al listado", `l:${mode}:${page}`)]);
  return { inline_keyboard: rows };
}
function listProcesses(data, followedOnly = false, page = 1) {
  const items = data.processes.filter(p => !followedOnly || p.followed).sort((a, b) => a.id.localeCompare(b.id));
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  if (!Number.isInteger(page) || page < 1 || page > pages) throw new Error(`Página caducada o inválida. Abre /${followedOnly ? "seguimientos" : "convocatorias"}.`);
  const mode = followedOnly ? "f" : "a";
  const visible = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const text = [`<b>${followedOnly ? "⭐ Tus seguimientos" : "💻 Convocatorias de informática"}</b>`, `Página ${page}/${pages} · ${items.length} procesos`,
    ...visible.map((p, i) => `<b>${i + 1}. ${clean(p.title, 110)}</b>\n${LABELS[p.category] || LABELS.REVIEW}${p.followed ? " · ⭐ Siguiendo" : ""}\n🏛️ ${clean(p.organisation, 80)}\n📍 ${clean(p.locality || p.province || p.scope, 55)} · destino por verificar\n📅 Solicitudes: ${p.deadline_confirmed ? dateOnly(p.deadline) : "pendientes de verificar"}`),
    ...(!items.length ? ["No hay convocatorias en esta lista."] : []),
    ...(data.sources?.some(s => s.status !== "OK") ? ["⚠️ Cobertura incompleta. Consulta Estado."] : []),
    ...(data.sources || []).filter(s => s.status === "PARTIAL").slice(0, 3).map(s => `⚠️ ${clean(s.source, 30)}: cobertura hasta ${dateOnly(s.last_covered_through)}; consulta parcial.`),
    `Actualizado: ${escapeHTML(localDate(data.generated_at))}`].join("\n\n");
  const rows = visible.map((p, i) => [button(`🔎 ${i + 1}. ${short(p.title, 45)}`, `d:${p.id}:${mode}:${page}`)]);
  const nav = [];
  if (page > 1) nav.push(button("◀️ Anterior", `l:${mode}:${page - 1}`));
  if (page < pages) nav.push(button("Siguiente ▶️", `l:${mode}:${page + 1}`));
  if (nav.length) rows.push(nav);
  rows.push([button(followedOnly ? "📋 Todas" : "⭐ Mis seguimientos", `l:${followedOnly ? "a" : "f"}:1`), button("ℹ️ Estado", "m:estado")]);
  return { text, markup: { inline_keyboard: rows } };
}
function processDetail(p, data) {
  const lines = [`💻 <b>${clean(p.title, 150)}</b>`, LABELS[p.category] || LABELS.REVIEW,
    `🏛️ ${clean(p.organisation, 130)}`, `📍 ${clean(p.locality || p.province || p.scope, 80)}\nDestino concreto: comprobar las bases.`,
    `🎓 <b>Titulación</b>: ${clean(p.qualification_text, 260)}`,
    `Grupo: ${clean(p.group, 12)} · Plazas: ${clean(p.positions, 12)}\nAcceso: ${clean(p.access, 30)} · ${clean(p.compatibility, 30)}`,
    ...(p.review_reason ? [`🔎 ${clean(p.review_reason, 180)}`] : []),
    `📅 <b>Solicitudes</b>: ${dateOnly(p.deadline)} (${p.deadline_confirmed ? "confirmada" : "sin verificar"})\nPublicación: ${dateOnly(p.publication_date)}`,
    `Estado: ${clean(p.status, 45)}`,
    `Examen: ${dateOnly(p.exam_date)}${p.exam_date_provisional ? " · PROVISIONAL" : ""}\nHora: ${clean(p.exam_time, 16)} · Lugar: ${clean(p.exam_place, 100)}`,
    `📝 ${clean(p.summary, 200)}`,
    ...(p.history?.length ? ["<b>Últimos cambios</b>", ...p.history.slice(0, 2).map(h => `${escapeHTML(localDate(h.date))}: ${clean(h.kind, 20)} · ${clean((h.changes || []).join(", "), 130)}`)] : []),
    ...((p.links || []).filter(l => safeURL(l.url) && escapeHTML(l.url).length <= 550).slice(0, 2).map(l => `<a href="${escapeHTML(safeURL(l.url))}">${clean(l.label || "Documento oficial", 60)}</a>`)),
    `Seguimiento: <b>${p.followed ? "activo" : "sin activar"}</b>`,
    "Seguir activa avisos; la inscripción se realiza en la sede oficial.",
    `Datos: ${escapeHTML(localDate(data.generated_at))}`];
  return lines.join("\n\n");
}

const RUN_STATES = {
  success: "✅ correcta", failure: "❌ fallida", in_progress: "⏳ en curso", queued: "⏳ en cola",
  cancelled: "cancelada", waiting: "esperando", pending: "pendiente", requested: "solicitada",
  timed_out: "tiempo agotado", startup_failure: "❌ no pudo arrancar", skipped: "omitida",
};

function localDate(value) {
  return value
    && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", dateStyle: "short", timeStyle: "medium" }).format(new Date(value))
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
    if (attempted && failed.length >= attempted) lines.push("❌ Búsqueda fallida.");
    else if (failed.length || delayed.length) lines.push("⚠️ Búsqueda completada con cobertura parcial.");
    else if (!attempted) lines.push("⚠️ No hay fuentes consultadas; cobertura sin acreditar.");
    else lines.push(Number(collection.created || 0) || Number(collection.updated || 0) ? "✅ Búsqueda correcta con novedades." : "✅ Búsqueda correcta sin novedades.");
    if (attempted && failed.length >= attempted) {
      lines.push("❌ No se pudo consultar ninguna fuente: eso NO significa que no haya novedades.");
    }
    lines.push(
      `Fuentes: ${attempted - failed.length - delayed.length} al día / ${delayed.length} con aviso de cobertura / ${failed.length} fallidas.`,
      `Registros examinados: ${collection.fetched ?? "?"}; nuevos procesos: ${collection.created ?? "?"}; actualizados: ${collection.updated ?? "?"}.`,
      "Documentos procesados: no medido por separado de los registros.",
      `Avisos pendientes en la consulta: ${collection.pending_notifications ?? "no disponible"}.`,
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
  let fullSuccess = null;
  const lines = [
    "Estado de las búsquedas (hora peninsular):",
    `• Última solicitada: ${localDate(newest.created_at)} — ${runState(newest)} (${runOrigin(newest)})`,
    `• Última iniciada: ${started ? localDate(started.run_started_at) : "no disponible"}`,
    `• Última completada: ${completed ? `${localDate(completed.updated_at)} — ${runState(completed)} (${runOrigin(completed)})` : "ninguna en las 20 últimas"}`,
    `• Última correcta en GitHub: ${succeeded ? localDate(succeeded.updated_at) : "ninguna en las 20 últimas"} (no acredita cobertura completa)`,
    active.length
      ? `• Búsqueda en marcha: sí (${runState(active[0])}, solicitada ${localDate(active[0].created_at)})`
      : "• Búsqueda en marcha: no",
    "",
  ];
  if (completed) {
    lines.push(`Resultado de la última completada (inicio ${localDate(completed.run_started_at || completed.created_at)}):`);
    try {
      const summaryResponse = await boundedFetch(`https://raw.githubusercontent.com/${encodeURIComponent(env.GITHUB_OWNER || "Alarbon")}/${encodeURIComponent(env.GITHUB_REPO || "bot_opos")}/${encodeURIComponent(env.GITHUB_REF || "main")}/data/latest_run.json?fresh=${Date.now()}`, { cache: "no-store" });
      if (summaryResponse.ok) {
        const report = await summaryResponse.json();
        if (String(report.run_id) === String(completed.id)) {
          lines.push(...reportLines(report));
          const collection = report.collection;
          if (completed.conclusion === "success" && collection?.sources_attempted > 0
              && !Object.keys(collection.errors || {}).length && !Object.keys(collection.warnings || {}).length) {
            fullSuccess = report.finished_at || completed.updated_at;
          }
        } else {
          lines.push("Resumen detallado de esa ejecución no disponible (puede no haberse guardado o la caché de GitHub aún no lo muestra); no se muestran estadísticas antiguas.");
        }
      } else lines.push("Resumen detallado no disponible.");
    } catch { lines.push("No se pudo cargar el resumen detallado."); }
    lines.push(completed.html_url);
  }
  if (active.length && active[0].html_url) lines.push("", `En curso: ${active[0].html_url}`);
  lines.push(`Última búsqueda con cobertura completa acreditada: ${fullSuccess ? localDate(fullSuccess) : "no disponible en el historial publicado"}.`);
  lines.push("", "Para iniciar una búsqueda: /buscar");
  return lines.join("\n");
}

function commandFrom(message) {
  const first = String(message || "").trim().split(/\s+/, 1)[0].toLowerCase();
  return first.split("@", 1)[0];
}

// Bounded isolate cache suppresses double taps/redelivery. Cross-isolate writes
// remain absolute (follow/unfollow), and the existing workflow serialises SQLite.
const pendingChanges = new Map();
function authorized(chatId, userId, env) {
  if (String(chatId) !== required(env, "TELEGRAM_CHAT_ID")) return false;
  const expected = String(env.TELEGRAM_USER_ID || (Number(chatId) > 0 ? chatId : ""));
  return !!expected && String(userId) === expected;
}
async function changeFollow(env, chatId, process, enabled) {
  const key = `${chatId}:${process.id}`;
  const now = Date.now();
  for (const [id, at] of pendingChanges) if (now - at > 600_000) pendingChanges.delete(id);
  if (pendingChanges.has(key)) {
    await reply(env, chatId, "⏳ Ya hay un cambio pendiente para esta convocatoria. Espera la confirmación y actualiza el listado.", menu());
    return;
  }
  if (!!process.followed === enabled) {
    await reply(env, chatId, enabled ? "⭐ El seguimiento ya está activo." : "🔕 El seguimiento ya está desactivado.", processActions(process));
    return;
  }
  // GitHub is shared by Worker isolates: don't enqueue another tracking change
  // while an earlier tracking workflow is visible as queued/running.
  // Lock before the first await: simultaneous callbacks in this isolate must
  // not both pass the active-run check before either dispatch is visible.
  pendingChanges.set(key, now);
  let runUrl;
  try {
    const runs = await workflowRuns({ ...env, GITHUB_WORKFLOW: "seguimiento.yml" }, 10);
    if (runs.some(run => ACTIVE_RUN_STATES.has(run.status))) {
      pendingChanges.delete(key);
      await reply(env, chatId, "⏳ Hay un cambio de seguimiento en curso. Espera su confirmación antes de solicitar otro.", menu());
      return;
    }
    runUrl = await dispatchSearch(env, enabled ? "follow" : "unfollow", process.id);
  } catch (error) {
    if (!error.uncertain) pendingChanges.delete(key);
    throw error;
  }
  await reply(env, chatId, `⏳ Cambio de seguimiento solicitado para ${short(process.title, 140)}. Se confirmará cuando GitHub lo guarde; todavía no está confirmado.\n${runUrl || "Consulta Seguimientos en unos minutos."}`, menu());
}
async function showList(env, chatId, data, mode, page, messageId) {
  const result = listProcesses(data, mode === "f", page);
  await reply(env, chatId, result.text, result.markup, messageId, true);
}
async function handleCallback(query, env) {
  const chatId = query.message?.chat?.id;
  if (!authorized(chatId, query.from?.id, env)) {
    await telegram(env, "answerCallbackQuery", { callback_query_id: query.id, text: "Acceso no autorizado", show_alert: true });
    return;
  }
  // Answer before GitHub/ catalogue access, so the Telegram spinner ends promptly.
  await telegram(env, "answerCallbackQuery", { callback_query_id: query.id });
  const value = String(query.data || "");
  try {
    if (value === "m:buscar" || value === "m:estado") {
      await handleUpdate({ message: { text: `/${value.slice(2)}`, chat: { id: chatId }, from: query.from } }, env);
      return;
    }
    if (new TextEncoder().encode(value).length > 64) throw new Error("Botón inválido. Abre /convocatorias.");
    const list = /^l:([af]):([1-9]\d{0,4})$/.exec(value);
    const action = /^([dfu]):([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}):([af]):([1-9]\d{0,4})$/.exec(value);
    if (!list && !action) throw new Error("Botón antiguo o inválido. Abre /convocatorias para actualizarlo.");
    const data = await catalog(env);
    if (list) await showList(env, chatId, data, list[1], Number(list[2]), query.message.message_id);
    else {
      const [, operation, id, mode, page] = action;
      // Callback IDs must be full, exact identifiers, including persistent aliases.
      const matches = data.processes.filter(p => p.id === id || (p.aliases || []).includes(id));
      if (matches.length !== 1) throw new Error("Esta convocatoria ya no está disponible. Abre /convocatorias.");
      const process = matches[0];
      if (operation === "d") await reply(env, chatId, processDetail(process, data), processActions(process, mode, Number(page)), query.message.message_id, true);
      else await changeFollow(env, chatId, process, operation === "f");
    }
  } catch (error) {
    if (!String(error.message).includes("message is not modified")) await reply(env, chatId, `❌ ${error.message || "No se pudo completar la consulta"}`, menu());
  }
}

async function handleUpdate(update, env, nowSeconds = Math.floor(Date.now() / 1000)) {
  // Edited messages are ignored on purpose: editing an old "/buscar" must not
  // trigger a new search.
  if (update?.callback_query) return handleCallback(update.callback_query, env);
  const message = update?.message;
  if (!message?.text || message?.chat?.id === undefined) return;

  const chatId = String(message.chat.id);
  const allowedChatId = required(env, "TELEGRAM_CHAT_ID");
  if (chatId !== allowedChatId) return;
  if (!authorized(chatId, message.from?.id, env)) return;
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
        await showList(env, chatId, data, command === "/seguimientos" ? "f" : "a", argument ? Number(argument) : 1);
      } else {
        if (!argument) {
          await showList(env, chatId, data, "a", 1);
          return;
        }
        const process = findProcess(data, argument);
        if (command === "/seguir" || command === "/dejar") {
          await changeFollow(env, chatId, process, command === "/seguir");
        } else await reply(env, chatId, processDetail(process, data), processActions(process), undefined, true);
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
      ].join("\n"), menu());
      return;
    }
    try {
      const runUrl = await dispatchSearch(env);
      await reply(env, chatId, [
        "📨 Búsqueda solicitada a GitHub Actions.",
        "Todavía no ha terminado (puede tardar unos segundos en empezar). Cuando acabe recibirás un resumen, también si no hay novedades.",
        "Consulta /estado para ver si está en marcha.",
        runUrl || "",
      ].join("\n"), menu());
    } catch (error) {
      await reply(env, chatId, `❌ No se ha podido solicitar la búsqueda. ${String(error.message || error)}`, menu());
    }
    return;
  }

  if (command === "/estado") {
    try {
      await reply(env, chatId, `<b>ℹ️ Estado</b>\n\n${clean(await latestStatus(env), 3500)}`, menu(), undefined, true);
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
      "💻 <b>Oposiciones de informática · Jaén</b>\n\nConsulta convocatorias, abre sus bases y activa avisos desde los botones.\n\n🔎 <b>Revisar</b>: requisitos o plazo sin confirmar.\n✅ <b>Abierta</b>: perfil y plazo confirmados.\n⭐ <b>Seguir</b>: recibir cambios; no realiza la inscripción.\n\nBuscar novedades solicita una consulta. Recibirás el resultado al terminar.\n\nTambién puedes usar /buscar, /estado, /convocatorias, /seguimientos y /detalle ID, /seguir ID o /dejar ID.",
      menu(), undefined, true,
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
