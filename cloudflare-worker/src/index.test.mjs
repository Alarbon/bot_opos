import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
const source = await readFile(new URL("./index.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

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
