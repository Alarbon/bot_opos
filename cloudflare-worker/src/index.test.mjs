import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./index.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

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

for (const command of ["/convocatorias", "/detalle aaaaaaaa", "/buscar aaaaaaaa", "/seguir aaaaaaaa", "/dejar aaaaaaaa", "/seguimientos"]) {
  test(`catalog command ${command}`, async () => {
    const originalFetch = globalThis.fetch;
    const messages = [];
    const dispatches = [];
    const process = { id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", title: "Técnico informático", organisation: "Martos", category: "REVIEW", followed: true, history: [], links: [], status: "DETECTADA" };
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
      assert.equal(dispatches.length, command.startsWith("/seguir ") || command.startsWith("/dejar ") ? 1 : 0);
      if (dispatches.length) assert.equal(dispatches[0].inputs.process_id, process.id);
    } finally { globalThis.fetch = originalFetch; }
  });
}
