import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
const source = await readFile(new URL("./index.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function currentBopDate(offsetDays = 0) {
  const value = new Date(Date.now() - offsetDays * 86_400_000);
  const day = String(value.getUTCDate()).padStart(2, "0");
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  return {
    path: `${day}-${month}-${value.getUTCFullYear()}`,
    iso: `${value.getUTCFullYear()}-${month}-${day}`,
  };
}

function officialDayHtml(day, edict = "4696") {
  return `<article><a href="https://bop.dipujaen.es/descargarws.dip?fechaBoletin=${day.iso}&amp;numeroEdicto=${edict}&amp;tipo=bop">PDF</a></article>`;
}

test("BOP proxy requires authentication before contacting the origin", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response("unexpected"); };
  try {
    const day = currentBopDate();
    const response = await worker.fetch(
      new Request(`https://example.com/bop/day/${day.path}`),
      { TELEGRAM_BOT_TOKEN: "private-token" },
      {},
    );
    assert.equal(response.status, 401);
    assert.equal(calls, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test("BOP day proxy returns the official HTML with its charset", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  const day = currentBopDate();
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    return new Response(officialDayHtml(day), {
      status: 200,
      headers: { "content-type": "text/html; charset=iso-8859-1" },
    });
  };
  try {
    const response = await worker.fetch(
      new Request(`https://example.com/bop/day/${day.path}`, { headers: { authorization: "Bearer private-token" } }),
      { TELEGRAM_BOT_TOKEN: "private-token" },
      {},
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-bop-proxy"), "1");
    assert.match(response.headers.get("content-type"), /iso-8859-1/);
    assert.equal(await response.text(), officialDayHtml(day));
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], `https://bop.dipujaen.es/bop/${day.path}`);
    assert.equal(calls[0][1].redirect, "manual");
  } finally { globalThis.fetch = originalFetch; }
});

test("BOP edict proxy only downloads a document listed by the official day", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  const day = currentBopDate();
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    if (calls.length === 1) {
      return new Response(officialDayHtml(day), {
        status: 200,
        headers: { "content-type": "text/html; charset=iso-8859-1" },
      });
    }
    return new Response("%PDF-safe", {
      status: 200,
      headers: { "content-type": "application/pdf", "content-length": "9" },
    });
  };
  try {
    const response = await worker.fetch(
      new Request(`https://example.com/bop/edict/${day.path}/4696`, { headers: { authorization: "Bearer private-token" } }),
      { TELEGRAM_BOT_TOKEN: "private-token" },
      {},
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "application/pdf");
    assert.equal(await response.text(), "%PDF-safe");
    assert.equal(calls.length, 2);
    const downloaded = new URL(calls[1][0]);
    assert.equal(downloaded.origin, "https://bop.dipujaen.es");
    assert.equal(downloaded.pathname, "/descargarws.dip");
    assert.equal(downloaded.searchParams.get("numeroEdicto"), "4696");
  } finally { globalThis.fetch = originalFetch; }
});

test("BOP proxy rejects invalid dates, unknown edicts and invalid upstream types", async () => {
  const originalFetch = globalThis.fetch;
  let mode = "unknown-edict";
  const day = currentBopDate();
  globalThis.fetch = async () => {
    if (mode === "unknown-edict") {
      return new Response(officialDayHtml(day, "1234"), { status: 200, headers: { "content-type": "text/html" } });
    }
    return new Response("not html", { status: 200, headers: { "content-type": "text/plain" } });
  };
  try {
    const headers = { authorization: "Bearer private-token" };
    const invalid = await worker.fetch(new Request("https://example.com/bop/day/31-02-2026", { headers }), { TELEGRAM_BOT_TOKEN: "private-token" }, {});
    assert.equal(invalid.status, 400);

    const missing = await worker.fetch(new Request(`https://example.com/bop/edict/${day.path}/9999`, { headers }), { TELEGRAM_BOT_TOKEN: "private-token" }, {});
    assert.equal(missing.status, 404);

    mode = "wrong-type";
    const wrongType = await worker.fetch(new Request(`https://example.com/bop/day/${day.path}`, { headers }), { TELEGRAM_BOT_TOKEN: "private-token" }, {});
    assert.equal(wrongType.status, 424);
    assert.equal(await wrongType.text(), "BOP_DEPENDENCY_DAY_CONTENT_TYPE");

    mode = "waf-html";
    globalThis.fetch = async () => new Response("<html><h1>Error temporal</h1></html>", { status: 200, headers: { "content-type": "text/html" } });
    const unrecognized = await worker.fetch(new Request(`https://example.com/bop/day/${day.path}`, { headers }), { TELEGRAM_BOT_TOKEN: "private-token" }, {});
    assert.equal(unrecognized.status, 424);
    assert.equal(await unrecognized.text(), "BOP_DEPENDENCY_DAY_UNRECOGNIZED");
  } finally { globalThis.fetch = originalFetch; }
});

test("BOP proxy represents an official missing day as covered", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(null, { status: 404 });
  try {
    const day = currentBopDate();
    const response = await worker.fetch(
      new Request(`https://example.com/bop/day/${day.path}`, { headers: { authorization: "Bearer private-token" } }),
      { TELEGRAM_BOT_TOKEN: "private-token" },
      {},
    );
    assert.equal(response.status, 204);
    assert.equal(response.headers.get("x-bop-proxy"), "1");
  } finally { globalThis.fetch = originalFetch; }
});

for (const [command, action] of [["/automatico_on", "enable"], ["/automatico_off", "disable"], ["/automatico_estado", null]]) {
  test(`removed automatic command ${command} does not mutate GitHub`, async () => {
    const original = globalThis.fetch;
    const calls = [], messages = [];
    globalThis.fetch = async (url, options) => {
      if (url.startsWith("https://api.github.com/")) {
        calls.push([url, options.method || "GET"]);
        return options.method === "PUT" ? new Response(null, { status: 204 }) : Response.json({ state: action === "disable" ? "disabled_manually" : "active" });
      }
      messages.push(JSON.parse(options.body).text);
      return Response.json({ ok: true, result: {} });
    };
    try {
      let pending;
      await worker.fetch(new Request("https://example.com/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "secret" }, body: JSON.stringify({ message: { from: { id: 1 }, text: command, chat: { id: 1 } } }) }), { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
      await pending;
      assert.equal(calls.length, 0);
      assert.equal(messages.length, 0);
    } finally { globalThis.fetch = original; }
  });
}


const ENV = {
  WEBHOOK_SECRET: "test-secret", TELEGRAM_CHAT_ID: "1",
  TELEGRAM_BOT_TOKEN: "test-token", GITHUB_TOKEN: "test-token",
};

async function sendUpdate(update, env = ENV, headers = { "x-telegram-bot-api-secret-token": "test-secret" }) {
  let pending;
  const response = await worker.fetch(new Request("https://example.com/telegram", {
    method: "POST", headers, body: JSON.stringify(update),
  }), env, { waitUntil(promise) { pending = promise; } });
  if (pending) await pending;
  return response;
}

function mockGitHub({ runs = [], dispatchStatus = 204, runsStatus = 200 } = {}) {
  const calls = { dispatches: [], listings: 0, messages: [] };
  globalThis.fetch = async (url, options = {}) => {
    if (url.startsWith("https://api.github.com/")) {
      if ((options.method || "GET") === "POST") {
        calls.dispatches.push(JSON.parse(options.body));
        return new Response(dispatchStatus === 204 ? null : JSON.stringify({ workflow_run_id: 123, html_url: "https://github.com/run/123" }), { status: dispatchStatus });
      }
      calls.listings++;
      return new Response(JSON.stringify({ workflow_runs: runs }), { status: runsStatus });
    }
    if (url.startsWith("https://raw.githubusercontent.com/")) return new Response("missing", { status: 404 });
    calls.messages.push(JSON.parse(options.body).text);
    return Response.json({ ok: true, result: {} });
  };
  return calls;
}

for (const status of [200, 204, 403, 422, 500]) {
  test(`/buscar reports a request (not a finished search) for GitHub HTTP ${status}`, async () => {
    const originalFetch = globalThis.fetch;
    const calls = mockGitHub({ dispatchStatus: status });
    try {
      const response = await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 } } });
      assert.equal(response.status, 200);
      assert.equal(calls.dispatches.length, 1);
      assert.deepEqual(calls.dispatches[0].inputs, { dry_run: false });
      assert.equal(calls.messages.length, 1);
      const accepted = status === 200 || status === 204;
      assert.ok(calls.messages[0].startsWith(accepted ? "📨" : "❌"));
      assert.doesNotMatch(calls.messages[0], /completad|iniciada/i);
      if (!accepted) assert.match(calls.messages[0], /No se ha iniciado nada/);
      if (status === 200) assert.match(calls.messages[0], /github\.com\/run\/123/);
    } finally { globalThis.fetch = originalFetch; }
  });
}

for (const state of ["queued", "in_progress", "waiting"]) {
  test(`/buscar does not queue a duplicate while a run is ${state}`, async () => {
    const originalFetch = globalThis.fetch;
    const calls = mockGitHub({ runs: [{ id: 9, status: state, created_at: "2026-10-30T07:30:00Z", html_url: "https://github.com/run/9" }] });
    try {
      await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 } } });
      assert.equal(calls.dispatches.length, 0);
      assert.match(calls.messages[0], /Ya hay una búsqueda/);
      assert.match(calls.messages[0], /run\/9/);
    } finally { globalThis.fetch = originalFetch; }
  });
}

test("/buscar still dispatches when the active-run check fails", async () => {
  const originalFetch = globalThis.fetch;
  const calls = mockGitHub({ runsStatus: 502 });
  try {
    await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 } } });
    assert.equal(calls.dispatches.length, 1);
    assert.ok(calls.messages[0].startsWith("📨"));
  } finally { globalThis.fetch = originalFetch; }
});

test("/buscar network failure is reported as unknown, not as success", async () => {
  const originalFetch = globalThis.fetch;
  const messages = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.startsWith("https://api.github.com/")) {
      if ((options.method || "GET") === "POST") throw new TypeError("network down");
      return Response.json({ workflow_runs: [] });
    }
    messages.push(JSON.parse(options.body).text);
    return Response.json({ ok: true, result: {} });
  };
  try {
    await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 } } });
    assert.match(messages[0], /^❌/);
    assert.match(messages[0], /No sé si la solicitud llegó/);
  } finally { globalThis.fetch = originalFetch; }
});

test("edited, stale, foreign and unauthenticated updates never dispatch", async () => {
  const originalFetch = globalThis.fetch;
  const calls = mockGitHub();
  try {
    const now = Math.floor(Date.now() / 1000);
    await sendUpdate({ edited_message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 }, date: now } });
    await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 }, date: now - 3600 } });
    await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 2 }, date: now } });
    const forbidden = await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 } } }, ENV, { "x-telegram-bot-api-secret-token": "wrong" });
    assert.equal(forbidden.status, 403);
    const missing = await sendUpdate({ message: { from: { id: 1 }, text: "/buscar", chat: { id: 1 } } }, ENV, {});
    assert.equal(missing.status, 403);
    assert.equal(calls.dispatches.length, 0);
    assert.equal(calls.listings, 0);
    assert.equal(calls.messages.length, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test("admin configure requires the webhook secret and subscribes to messages and callbacks", async () => {
  const originalFetch = globalThis.fetch;
  const telegramCalls = [];
  globalThis.fetch = async (url, options) => {
    telegramCalls.push([url.split("/").pop(), JSON.parse(options.body)]);
    return Response.json({ ok: true, result: true });
  };
  try {
    const denied = await worker.fetch(new Request("https://example.com/admin/configure", { method: "POST", headers: { authorization: "Bearer nope" } }), ENV, {});
    assert.equal(denied.status, 403);
    assert.equal(telegramCalls.length, 0);
    const ok = await worker.fetch(new Request("https://example.com/admin/configure", { method: "POST", headers: { authorization: "Bearer test-secret" } }), ENV, {});
    assert.equal(ok.status, 200);
    const webhook = telegramCalls.find(([method]) => method === "setWebhook")[1];
    assert.deepEqual(webhook.allowed_updates, ["message", "callback_query"]);
    assert.equal(webhook.secret_token, "test-secret");
  } finally { globalThis.fetch = originalFetch; }
});

test("/estado distinguishes requested, started, completed, successful and running", async () => {
  const originalFetch = globalThis.fetch;
  const runs = [
    { id: 3, event: "workflow_dispatch", status: "queued", created_at: "2026-10-30T09:00:00Z", html_url: "https://github.com/run/3" },
    { id: 2, event: "schedule", status: "completed", conclusion: "failure", created_at: "2026-10-30T06:30:00Z", run_started_at: "2026-10-30T06:31:00Z", updated_at: "2026-10-30T06:35:00Z", html_url: "https://github.com/run/2" },
    { id: 1, event: "workflow_dispatch", status: "completed", conclusion: "success", created_at: "2026-10-29T17:00:00Z", run_started_at: "2026-10-29T17:00:10Z", updated_at: "2026-10-29T17:04:00Z", html_url: "https://github.com/run/1" },
  ];
  const messages = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.startsWith("https://api.github.com/")) return Response.json({ workflow_runs: runs });
    if (url.startsWith("https://raw.githubusercontent.com/")) {
      return Response.json({ run_id: "2", collection: { sources_attempted: 3, errors: { boe: "x", bop_jaen: "y", iaap: "z" }, warnings: {}, fetched: 0, created: 0, updated: 0 }, delivery: null });
    }
    messages.push(JSON.parse(options.body).text);
    return Response.json({ ok: true, result: {} });
  };
  try {
    await sendUpdate({ message: { from: { id: 1 }, text: "/estado", chat: { id: 1 } } });
    const text = messages[0];
    assert.match(text, /Última solicitada: 30\/10\/26, 10:00:00 — ⏳ en cola \(manual\)/);
    assert.match(text, /Última iniciada: 30\/10\/26, 7:31:00/);
    assert.match(text, /Última completada: 30\/10\/26, 7:35:00 — ❌ fallida \(automática programada\)/);
    assert.match(text, /Última correcta en GitHub: 29\/10\/26, 18:04:00/);
    assert.match(text, /Búsqueda en marcha: sí/);
    assert.match(text, /No se pudo consultar ninguna fuente/);
    assert.match(text, /Envío de avisos no registrado/);
  } finally { globalThis.fetch = originalFetch; }
});

for (const matchingReport of [true, false]) {
  test(`/estado uses local dates and ignores stale summary (${matchingReport})`, async () => {
    const originalFetch = globalThis.fetch;
    const messages = [];
    globalThis.fetch = async (url, options) => {
      if (url.startsWith("https://api.github.com/")) return Response.json({ workflow_runs: [{ id: 42, event: "schedule", status: "completed", conclusion: "success", run_started_at: "2026-10-06T08:30:00Z", updated_at: "2026-10-06T08:32:00Z", html_url: "https://github.com/run/42" }] });
      if (url.startsWith("https://raw.githubusercontent.com/")) return Response.json({ run_id: matchingReport ? "42" : "41", collection: { sources_attempted: 8, errors: { bop_jaen: "500" }, fetched: 12, created: 0, updated: 1 }, delivery: { sent: 0, retryable: 0, uncertain: 0 } });
      messages.push(JSON.parse(options.body).text);
      return Response.json({ ok: true, result: {} });
    };
    try {
      let pending;
      await worker.fetch(new Request("https://example.com/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "secret" }, body: JSON.stringify({ message: { from: { id: 1 }, text: "/estado", chat: { id: 1 } } }) }), { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
      await pending;
      assert.match(messages[0], /automática programada/);
      assert.match(messages[0], /10:30:00/);
      assert.equal(messages[0].includes("bop_jaen"), matchingReport);
      if (!matchingReport) assert.match(messages[0], /no se muestran estadísticas antiguas/);
    } finally { globalThis.fetch = originalFetch; }
  });
}

for (const command of ["/convocatorias", "/detalle", "/detalle aaaaaaaa", "/detalle bbbbbbbb", "/buscar aaaaaaaa", "/seguir aaaaaaaa", "/dejar aaaaaaaa", "/seguimientos"]) {
  test(`catalog command ${command}`, async () => {
    const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source + "\n//" + command).toString("base64")}`);
    const originalFetch = globalThis.fetch;
    const messages = [];
    const dispatches = [];
    const process = { id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", aliases: ["bbbbbbbb-cccc-dddd-eeee-ffffffffffff"], title: "Técnico informático", organisation: "Martos", category: "REVIEW", followed: command.startsWith("/dejar ") || command === "/seguimientos", history: [], links: [], status: "DETECTADA" };
    globalThis.fetch = async (url, options) => {
      if (url.startsWith("https://raw.githubusercontent.com/")) return Response.json({ processes: [process], generated_at: "2026-10-06", sources: [] });
      if (url.startsWith("https://api.github.com/")) {
        if (!options.body) return Response.json({ workflow_runs: [] });
        dispatches.push(JSON.parse(options.body));
        return Response.json({ html_url: "https://github.com/test/run" });
      }
      messages.push(JSON.parse(options.body).text);
      return Response.json({ ok: true, result: {} });
    };
    try {
      let pending;
      await worker.fetch(new Request("https://example.com/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "secret" }, body: JSON.stringify({ message: { from: { id: 1 }, text: command, chat: { id: 1 } } }) }), { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
      await pending;
      assert.equal(messages.length, 1);
      assert.ok(!messages[0].startsWith("❌"));
      if (!command.startsWith("/seguir ") && !command.startsWith("/dejar ")) {
        assert.ok(messages[0].includes("No confirmado"));
        assert.ok(messages[0].includes("destino") || messages[0].includes("Destino"));
      }
      assert.equal(dispatches.length, command.startsWith("/seguir ") || command.startsWith("/dejar ") ? 1 : 0);
      if (dispatches.length) assert.equal(dispatches[0].inputs.process_id, process.id);
    } finally { globalThis.fetch = originalFetch; }
  });
}

test("/convocatorias explains an official source delay without calling it failed", async () => {
  const originalFetch = globalThis.fetch;
  const messages = [];
  globalThis.fetch = async (url, options) => {
    if (url.startsWith("https://raw.githubusercontent.com/")) {
      return Response.json({
        processes: [],
        generated_at: "2026-10-06T20:00:00Z",
        sources: [{
          source: "bop_jaen",
          status: "PARTIAL",
          last_covered_through: "2026-10-05",
          error: "BOP de Jaén: archivo oficial disponible hasta 2026-10-05; 2026-10-06 pendiente de indexación.",
        }],
      });
    }
    messages.push(JSON.parse(options.body).text);
    return Response.json({ ok: true, result: {} });
  };
  try {
    let pending;
    await worker.fetch(new Request("https://example.com/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "secret" },
      body: JSON.stringify({ message: { from: { id: 1 }, text: "/convocatorias", chat: { id: 1 } } }),
    }), {
      WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test",
    }, { waitUntil(p) { pending = p; } });
    await pending;
    assert.match(messages[0], /cobertura hasta 05\/10\/2026/);
    assert.doesNotMatch(messages[0], /fuentes fallaron/i);
  } finally { globalThis.fetch = originalFetch; }
});

test("/estado reports delayed coverage separately from failures", async () => {
  const originalFetch = globalThis.fetch;
  const messages = [];
  globalThis.fetch = async (url, options) => {
    if (url.startsWith("https://api.github.com/")) {
      return Response.json({ workflow_runs: [{
        id: 50, event: "workflow_dispatch", status: "completed", conclusion: "success",
        run_started_at: "2026-10-06T20:00:00Z", updated_at: "2026-10-06T20:02:00Z",
        html_url: "https://github.com/run/50",
      }] });
    }
    if (url.startsWith("https://raw.githubusercontent.com/")) {
      return Response.json({
        run_id: "50",
        collection: {
          sources_attempted: 10, errors: {},
          warnings: { bop_jaen: "archivo oficial disponible hasta 2026-10-05" },
          fetched: 21, created: 0, updated: 0,
        },
        delivery: { sent: 0, retryable: 0, uncertain: 0 },
      });
    }
    messages.push(JSON.parse(options.body).text);
    return Response.json({ ok: true, result: {} });
  };
  try {
    let pending;
    await worker.fetch(new Request("https://example.com/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "secret" },
      body: JSON.stringify({ message: { from: { id: 1 }, text: "/estado", chat: { id: 1 } } }),
    }), {
      WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test",
    }, { waitUntil(p) { pending = p; } });
    await pending;
    assert.match(messages[0], /9 al día \/ 1 con aviso de cobertura \/ 0 fallidas/);
    assert.match(messages[0], /bop_jaen: archivo oficial disponible hasta 2026-10-05/);
  } finally { globalThis.fetch = originalFetch; }
});


let harnessSequence = 0;
async function withBot(run, { followed = false, active = false, count = 6, hostile = false, catalogStatus = 200, envOverrides = {} } = {}) {
  const { default: isolated } = await import(`data:text/javascript;base64,${Buffer.from(source + "\n//harness" + ++harnessSequence).toString("base64")}`);
  const processes = Array.from({ length: count }, (_, i) => ({
    id: `${String(i + 1).padStart(8, "0")}-1111-2222-3333-444444444444`, aliases: [],
    title: hostile ? "<&>".repeat(1000) : `Técnico informático ${i + 1}`, organisation: hostile ? "&".repeat(2000) : "Ayuntamiento de Jaén",
    qualification_text: hostile ? "<".repeat(2000) : "No verificada", scope: "Andalucía", category: "REVIEW", followed,
    summary: hostile ? ">".repeat(2000) : "Requisitos pendientes", links: [], url: "https://www.juntadeandalucia.es/bases",
  }));
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), body: options.body ? JSON.parse(options.body) : null, method: options.method || "GET" });
    assert.ok(options.signal, "outbound request must have a timeout");
    if (String(url).startsWith("https://raw.githubusercontent.com/")) return Response.json({ processes, generated_at: "2026-11-01T10:00:00Z" }, { status: catalogStatus });
    if (String(url).includes("/runs?")) return Response.json({ workflow_runs: active ? [{ status: "queued" }] : [] });
    if (String(url).includes("/dispatches")) return new Response(null, { status: 204 });
    return Response.json({ ok: true, result: { message_id: 100 } });
  };
  async function update(value, secret = "secret") {
    let pending;
    const response = await isolated.fetch(new Request("https://test/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": secret }, body: JSON.stringify(value) }),
      { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test", ...envOverrides }, { waitUntil(p) { pending = p; } });
    if (pending) await pending;
    return response;
  }
  const click = (data, user = 1, chat = 1) => update({ callback_query: { id: `query${calls.length}`, from: { id: user }, data, message: { message_id: 70, date: 1, chat: { id: chat } } } });
  try { await run({ calls, processes, click, update }); } finally { globalThis.fetch = original; }
}
const apiCalls = (calls, method) => calls.filter(c => c.url.endsWith(`/${method}`));

test("catalog pagination edits one message and retains full stable IDs", () => withBot(async ({ calls, click, processes }) => {
  await click("l:a:1");
  const first = apiCalls(calls, "editMessageText")[0].body;
  assert.equal(first.parse_mode, "HTML");
  assert.match(first.text, /Página 1\/2/);
  const rows = first.reply_markup.inline_keyboard;
  assert.equal(rows[0][0].callback_data, `d:${processes[0].id}:a:1`);
  assert.equal(rows[5][0].callback_data, "l:a:2");
  await click("l:a:2");
  assert.match(apiCalls(calls, "editMessageText")[1].body.text, /Página 2\/2/);
  assert.equal(apiCalls(calls, "editMessageText")[1].body.reply_markup.inline_keyboard[1][0].callback_data, "l:a:1");
  assert.equal(apiCalls(calls, "sendMessage").length, 0);
  assert.equal(apiCalls(calls, "dispatches").length, 0);
}));

test("details answer the callback before reading catalogue, source and full copy command work", () => withBot(async ({ calls, click, processes }) => {
  await click(`d:${processes[0].id}:f:1`);
  assert.match(calls[0].url, /answerCallbackQuery$/);
  const message = apiCalls(calls, "editMessageText")[0].body;
  const buttons = message.reply_markup.inline_keyboard.flat();
  assert.ok(buttons.some(b => b.url === processes[0].url));
  assert.ok(buttons.some(b => b.copy_text?.text === `/detalle ${processes[0].id}`));
  assert.ok(buttons.some(b => b.callback_data === "l:f:1"));
  for (const b of buttons) if (b.callback_data) assert.ok(Buffer.byteLength(b.callback_data) <= 64);
  assert.equal(calls.filter(c => c.url.startsWith("https://api.github.com")).length, 0);
}));

for (const [user, chat] of [[9, 1], [1, 9]]) test(`callbacks authorize user ${user} and chat ${chat} before reads`, () => withBot(async ({ calls, click }) => {
  await click("l:a:1", user, chat);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.text, "Acceso no autorizado");
}));

test("webhook authenticity also protects callback queries", () => withBot(async ({ calls, update }) => {
  const response = await update({ callback_query: { data: "m:buscar" } }, "wrong");
  assert.equal(response.status, 403); assert.equal(calls.length, 0);
}));

for (const bad of ["d:00000001:a:1", "d:" + "x".repeat(65), "l:a:0", "old-button"]) test(`invalid callback is answered and cannot dispatch: ${bad.slice(0, 20)}`, () => withBot(async ({ calls, click }) => {
  await click(bad);
  assert.equal(apiCalls(calls, "answerCallbackQuery").length, 1);
  assert.equal(apiCalls(calls, "dispatches").length, 0);
  assert.match(apiCalls(calls, "sendMessage")[0].body.text, /inválido|antiguo/);
}));

test("expired exact process IDs fail safely", () => withBot(async ({ calls, click }) => {
  await click("d:99999999-1111-2222-3333-444444444444:a:1");
  assert.match(apiCalls(calls, "sendMessage")[0].body.text, /ya no está disponible/);
  assert.equal(apiCalls(calls, "dispatches").length, 0);
}));

for (const followed of [false, true]) test(`${followed ? "unfollow" : "follow"} uses its own workflow and suppresses double taps`, () => withBot(async ({ calls, click, processes }) => {
  const action = `${followed ? "u" : "f"}:${processes[0].id}:a:1`;
  await click(action); await click(action);
  const dispatches = apiCalls(calls, "dispatches");
  assert.equal(dispatches.length, 1);
  assert.match(dispatches[0].url, /seguimiento.yml/);
  assert.equal(dispatches[0].body.inputs.operation, followed ? "unfollow" : "follow");
  assert.equal(dispatches[0].body.inputs.process_id, processes[0].id);
  const replies = apiCalls(calls, "sendMessage");
  assert.match(replies[0].body.text, /todavía no está confirmado/);
  assert.match(replies[1].body.text, /pendiente/);
}, { followed }));

test("visible active tracking workflow suppresses changes across isolates", () => withBot(async ({ calls, click, processes }) => {
  await click(`f:${processes[0].id}:a:1`);
  assert.equal(apiCalls(calls, "dispatches").length, 0);
  assert.match(apiCalls(calls, "sendMessage")[0].body.text, /en curso/);
}, { active: true }));

test("HTML hostile catalogue fields remain escaped and messages fit Telegram", () => withBot(async ({ calls, click, processes }) => {
  await click("l:a:1"); await click(`d:${processes[0].id}:a:1`);
  for (const call of apiCalls(calls, "editMessageText")) {
    assert.ok(call.body.text.length < 4096);
    assert.doesNotMatch(call.body.text, /<&>/);
    assert.match(call.body.text, /&lt;|&amp;/);
  }
}, { hostile: true }));

test("catalogue aliases resolve old message buttons without prefix guessing", () => withBot(async ({ calls, click, processes }) => {
  const old = "abcdef01-1111-2222-3333-444444444444";
  processes[0].aliases.push(old);
  await click(`d:${old}:a:1`);
  assert.equal(apiCalls(calls, "editMessageText").length, 1);
  assert.equal(apiCalls(calls, "dispatches").length, 0);
}));


for (const state of ["OK", "PARTIAL", "NO_SOURCES"]) test(`/estado distinguishes source outcome ${state}`, async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith("https://api.github.com/")) return Response.json({ workflow_runs: [{ id: 90, status: "completed", conclusion: "success", updated_at: "2026-11-01T10:00:00Z" }] });
    if (String(url).startsWith("https://raw.githubusercontent.com/")) return Response.json({ run_id: 90, collection: { sources_attempted: 1, created: 0, updated: 0, errors: state === "NO_SOURCES" ? { boja: "400" } : {}, warnings: state === "PARTIAL" ? { iaap: "documento pendiente" } : {} } });
    calls.push(JSON.parse(options.body)); return Response.json({ ok: true, result: {} });
  };
  try {
    let pending;
    await worker.fetch(new Request("https://test/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "s" }, body: JSON.stringify({ message: { from: { id: 1 }, text: "/estado", chat: { id: 1 } } }) }),
      { WEBHOOK_SECRET: "s", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
    await pending;
    assert.match(calls[0].text, state === "OK" ? /correcta sin novedades/ : state === "PARTIAL" ? /cobertura parcial/ : /Búsqueda fallida/);
    assert.equal(calls[0].parse_mode, "HTML");
    assert.ok(calls[0].reply_markup);
  } finally { globalThis.fetch = original; }
});


test("simultaneous follow callbacks in one isolate dispatch once", () => withBot(async ({ calls, click, processes }) => {
  const action = `f:${processes[0].id}:a:1`;
  await Promise.all([click(action), click(action)]);
  assert.equal(apiCalls(calls, "answerCallbackQuery").length, 2);
  assert.equal(apiCalls(calls, "dispatches").length, 1);
}));

for (const status of [404, 503]) test(`unavailable catalogue ${status} ends callback without dispatch`, () => withBot(async ({ calls, click }) => {
  await click("l:a:1");
  assert.equal(apiCalls(calls, "answerCallbackQuery").length, 1);
  assert.match(apiCalls(calls, "sendMessage")[0].body.text, /Catálogo todavía no disponible/);
  assert.equal(apiCalls(calls, "dispatches").length, 0);
}, { catalogStatus: status }));

for (const configured of [false, true]) test(`group callback requires explicit allowed user (${configured})`, () => withBot(async ({ calls, click }) => {
  await click("l:a:1", 1, -100);
  if (configured) assert.equal(apiCalls(calls, "editMessageText").length, 1);
  else {
    assert.equal(calls.length, 1);
    assert.match(calls[0].body.text, /Acceso no autorizado/);
  }
  assert.equal(apiCalls(calls, "dispatches").length, 0);
}, { envOverrides: { TELEGRAM_CHAT_ID: "-100", ...(configured ? { TELEGRAM_USER_ID: "1" } : {}) } }));
