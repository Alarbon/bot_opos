const TELEGRAM_API = "https://api.telegram.org";
const GITHUB_API = "https://api.github.com";

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

async function telegram(env, method, payload) {
  const token = required(env, "TELEGRAM_BOT_TOKEN");
  const response = await fetch(`${TELEGRAM_API}/bot${token}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(`Telegram rechazo ${method}: ${data.description || response.status}`);
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
    allowed_updates: ["message", "edited_message"],
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

async function dispatchSearch(env, operation = null, processId = null) {
  const owner = String(env.GITHUB_OWNER || "Alarbon").trim();
  const repo = String(env.GITHUB_REPO || "bot_opos").trim();
  const workflow = operation ? "seguimiento.yml" : String(env.GITHUB_WORKFLOW || "oposiciones.yml").trim();
  const ref = String(env.GITHUB_REF || "main").trim();
  const token = required(env, "GITHUB_TOKEN");
  const url = `${GITHUB_API}/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      "user-agent": "oposiciones-telegram-worker/1.0",
      "x-github-api-version": "2026-03-10",
    },
    body: JSON.stringify({ ref, inputs: operation ? { operation, process_id: processId } : { dry_run: false } }),
  });
  if (response.status !== 200 && response.status !== 204) {
    const detail = await response.text();
    throw new Error(`GitHub no inicio el workflow (${response.status}): ${detail.slice(0, 300)}`);
  }
  return response.status === 200 ? (await response.json()).html_url : null;
}

async function catalog(env) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "Alarbon");
  const repo = encodeURIComponent(env.GITHUB_REPO || "bot_opos");
  const ref = encodeURIComponent(env.GITHUB_REF || "main");
  const response = await fetch(`https://raw.githubusercontent.com/${owner}/${repo}/${ref}/data/catalog.json`, { cache: "no-store" });
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

async function latestStatus(env) {
  const owner = String(env.GITHUB_OWNER || "Alarbon").trim();
  const repo = String(env.GITHUB_REPO || "bot_opos").trim();
  const workflow = String(env.GITHUB_WORKFLOW || "oposiciones.yml").trim();
  const token = required(env, "GITHUB_TOKEN");
  const response = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/actions/workflows/${workflow}/runs?per_page=1`,
    {
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${token}`,
        "user-agent": "oposiciones-telegram-worker/1.0",
        "x-github-api-version": "2026-03-10",
      },
    },
  );
  if (!response.ok) throw new Error(`GitHub devolvio ${response.status}`);
  const data = await response.json();
  const run = data.workflow_runs?.[0];
  if (!run) return "Todavía no hay ejecuciones registradas.";
  const state = run.status === "completed" ? run.conclusion : run.status;
  const states = { success: "✅ Completada", failure: "❌ Fallida", in_progress: "⏳ En curso", queued: "⏳ En cola", cancelled: "Cancelada", waiting: "Esperando", timed_out: "Tiempo agotado" };
  const localDate = value => value ? new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", dateStyle: "short", timeStyle: "medium" }).format(new Date(value)) : "No registrada";
  const lines = ["Última búsqueda: " + (states[state] || state || "desconocido"),
    `Origen: ${run.event === "schedule" ? "automática programada" : run.event === "workflow_dispatch" ? "manual" : run.event}`,
    `Inicio: ${localDate(run.run_started_at || run.created_at)} (hora peninsular)`,
    ...(run.status === "completed" ? [`Fin registrado por GitHub: ${localDate(run.updated_at)}`] : []),
    "Horario: 08:30, 12:30, 17:30 y 21:00 · Europe/Madrid.",
  ];
  try {
    const summaryResponse = await fetch(`https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${encodeURIComponent(env.GITHUB_REF || "main")}/data/latest_run.json`, { cache: "no-store" });
    if (summaryResponse.ok) {
      const report = await summaryResponse.json();
      if (String(report.run_id) === String(run.id)) {
        const collection = report.collection;
        if (collection) {
          const failed = Object.keys(collection.errors || {});
          lines.push(`Fuentes: ${collection.sources_attempted - failed.length} correctas / ${failed.length} fallidas.`,
            `Registros examinados: ${collection.fetched}; nuevos procesos: ${collection.created}; actualizados: ${collection.updated}.`);
          if (failed.length) lines.push("⚠️ Cobertura incompleta. Pendientes: " + failed.join(", "));
        } else lines.push("No se completó la consulta de fuentes.");
        if (report.delivery) lines.push(`Avisos enviados: ${report.delivery.sent}; reintentos pendientes: ${report.delivery.retryable}; entregas inciertas: ${report.delivery.uncertain}.`);
      } else lines.push("Resumen de esta ejecución todavía no disponible; no se muestran estadísticas antiguas.");
    } else lines.push("Resumen detallado todavía no disponible.");
  } catch { lines.push("No se pudo cargar el resumen detallado."); }
  lines.push(run.html_url);
  return lines.join("\n");
}

function commandFrom(message) {
  const first = String(message || "").trim().split(/\s+/, 1)[0].toLowerCase();
  return first.split("@", 1)[0];
}

async function handleUpdate(update, env) {
  const message = update?.message || update?.edited_message;
  if (!message?.text || message?.chat?.id === undefined) return;

  const chatId = String(message.chat.id);
  const allowedChatId = required(env, "TELEGRAM_CHAT_ID");
  if (chatId !== allowedChatId) return;

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
    await reply(env, chatId, "🔎 Solicitud recibida. Estoy iniciando la búsqueda en GitHub Actions.");
    try {
      const runUrl = await dispatchSearch(env);
      await reply(
        env,
        chatId,
        `✅ Búsqueda iniciada. Usa /estado para ver cuándo termina y /convocatorias para consultar los resultados, aunque no haya novedades.\n${runUrl || ""}`,
      );
    } catch (error) {
      await reply(env, chatId, `❌ No pude iniciar la búsqueda: ${String(error.message || error)}`);
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
        "Además, las búsquedas programadas siguen funcionando automáticamente.",
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

    if (request.method === "POST" && url.pathname === "/admin/configure") {
      const expectedSecret = required(env, "WEBHOOK_SECRET");
      const receivedSecret = request.headers.get("authorization") || "";
      if (receivedSecret !== `Bearer ${expectedSecret}`) return textResponse("forbidden", 403);

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
    if (receivedSecret !== expectedSecret) return textResponse("forbidden", 403);

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
