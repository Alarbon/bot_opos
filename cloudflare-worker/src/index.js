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
      { command: "ayuda", description: "Mostrar la ayuda" },
    ],
  });
}

async function dispatchSearch(env) {
  const owner = String(env.GITHUB_OWNER || "Alarbon").trim();
  const repo = String(env.GITHUB_REPO || "bot_opos").trim();
  const workflow = String(env.GITHUB_WORKFLOW || "oposiciones.yml").trim();
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
    body: JSON.stringify({ ref, inputs: { dry_run: false } }),
  });
  if (response.status !== 204) {
    const detail = await response.text();
    throw new Error(`GitHub no inicio el workflow (${response.status}): ${detail.slice(0, 300)}`);
  }
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
  return `Última ejecución: ${state || "desconocido"}\n${run.html_url}`;
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
  if (command === "/buscar") {
    await reply(env, chatId, "🔎 Solicitud recibida. Estoy iniciando la búsqueda en GitHub Actions.");
    try {
      await dispatchSearch(env);
      await reply(
        env,
        chatId,
        "✅ Búsqueda iniciada. Te enviaré aquí las convocatorias nuevas cuando termine.",
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
    await reply(
      env,
      chatId,
      [
        "🤖 Bot de oposiciones de informática",
        "",
        "/buscar — iniciar una búsqueda ahora",
        "/estado — consultar la última ejecución",
        "/ayuda — mostrar esta ayuda",
        "",
        "Además, las búsquedas programadas siguen funcionando automáticamente.",
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
