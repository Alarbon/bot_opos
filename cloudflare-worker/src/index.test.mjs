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
