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
      await worker.fetch(new Request("https://example.com/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "secret" }, body: JSON.stringify({ message: { text: command, chat: { id: 1 } } }) }), { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
      await pending;
      assert.equal(calls.length, 0);
      assert.equal(messages.length, 0);
    } finally { globalThis.fetch = original; }
  });
}


for (const status of [200, 204, 403]) {
  test(`/buscar handles GitHub HTTP ${status}`, async () => {
    const originalFetch = globalThis.fetch;
    const messages = [];
    let dispatches = 0;
    globalThis.fetch = async (url, options) => {
      if (url.startsWith("https://api.github.com/")) {
        dispatches++;
        return new Response(status === 204 ? null : JSON.stringify({ workflow_run_id: 123 }), { status });
      }
      messages.push(JSON.parse(options.body).text);
      return Response.json({ ok: true, result: {} });
    };
    try {
      let pending;
      const response = await worker.fetch(new Request("https://example.com/telegram", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": "test-secret" },
        body: JSON.stringify({ message: { text: "/buscar", chat: { id: 1 } } }),
      }), {
        WEBHOOK_SECRET: "test-secret", TELEGRAM_CHAT_ID: "1",
        TELEGRAM_BOT_TOKEN: "test-token", GITHUB_TOKEN: "test-token",
      }, { waitUntil(promise) { pending = promise; } });
      await pending;
      assert.equal(response.status, 200);
      assert.equal(dispatches, 1);
      assert.equal(messages.length, 2);
      assert.ok(messages[1].startsWith(status === 403 ? "❌" : "✅"));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
}

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
      await worker.fetch(new Request("https://example.com/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "secret" }, body: JSON.stringify({ message: { text: "/estado", chat: { id: 1 } } }) }), { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
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
    const originalFetch = globalThis.fetch;
    const messages = [];
    const dispatches = [];
    const process = { id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", aliases: ["bbbbbbbb-cccc-dddd-eeee-ffffffffffff"], title: "Técnico informático", organisation: "Martos", category: "REVIEW", followed: true, history: [], links: [], status: "DETECTADA" };
    globalThis.fetch = async (url, options) => {
      if (url.startsWith("https://raw.githubusercontent.com/")) return Response.json({ processes: [process], generated_at: "2026-10-06", sources: [] });
      if (url.startsWith("https://api.github.com/")) {
        dispatches.push(JSON.parse(options.body));
        return Response.json({ html_url: "https://github.com/test/run" });
      }
      messages.push(JSON.parse(options.body).text);
      return Response.json({ ok: true, result: {} });
    };
    try {
      let pending;
      await worker.fetch(new Request("https://example.com/telegram", { method: "POST", headers: { "x-telegram-bot-api-secret-token": "secret" }, body: JSON.stringify({ message: { text: command, chat: { id: 1 } } }) }), { WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test" }, { waitUntil(p) { pending = p; } });
      await pending;
      assert.equal(messages.length, 1);
      assert.ok(!messages[0].startsWith("❌"));
      if (!command.startsWith("/seguir ") && !command.startsWith("/dejar ")) {
        assert.ok(messages[0].includes("Municipio: No confirmado"));
        assert.ok(messages[0].includes("Provincia: No confirmada") || messages[0].includes("Provincia: No confirmado"));
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
      body: JSON.stringify({ message: { text: "/convocatorias", chat: { id: 1 } } }),
    }), {
      WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test",
    }, { waitUntil(p) { pending = p; } });
    await pending;
    assert.match(messages[0], /archivo oficial disponible hasta 2026-10-05/);
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
      body: JSON.stringify({ message: { text: "/estado", chat: { id: 1 } } }),
    }), {
      WEBHOOK_SECRET: "secret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "test", GITHUB_TOKEN: "test",
    }, { waitUntil(p) { pending = p; } });
    await pending;
    assert.match(messages[0], /9 al día \/ 1 con aviso de cobertura \/ 0 fallidas/);
    assert.match(messages[0], /bop_jaen: archivo oficial disponible hasta 2026-10-05/);
  } finally { globalThis.fetch = originalFetch; }
});
