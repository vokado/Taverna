import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { resolve } from "node:path";

test("Локальная сеть: отдельное подтверждение, один запрос и проверяемый исход", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(120000);
  let count = 0;
  const server = createServer((_req, res) => {
    count++;
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("approved-once");
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const addr = server.address() as AddressInfo;
  const url = `http://127.0.0.1:${addr.port}/approved`;
  try {
    const pluginId = crypto.randomUUID();
    const manifest = {
      format: 1, id: pluginId, name: "Локальная сеть",
      version: "1.0.0", api_version: 1, runtime: "declarative", entry: "main.json",
      requests: [{ capability: "network.request", reason: "Запрос к этому адресу", scopes: [url] }],
    };
    const plan = {
      format: 1,
      message: "Сеть только по подтверждению",
      actions: [{ type: "network.request", url, method: "GET", timeout_seconds: 5 }],
    };
    const python = process.env.TAVERNA_PYTHON || "python";
    const zip = execFileSync(python, [resolve("e2e/support/plugin_fixture.py")], {
      input: JSON.stringify({ manifest, source: JSON.stringify(plan) }),
    });
    const csrf = (await (await page.request.get(appUrl + "/api/v1/session")).json()).csrf;
    const headers = { "X-Taverna-CSRF": csrf };
    const looked = await page.request.post(appUrl + "/api/v1/plugins/import-preview", {
      headers, data: zip,
    });
    expect(looked.ok(), await looked.text()).toBe(true);
    const q = await looked.json();
    const params = new URLSearchParams({
      expected_revision: String(q.expected_revision),
      catalog_revision: String(q.catalog_revision),
      approved_sha256: q.sha256,
      confirmed: "true",
    });
    const installed = await page.request.post(appUrl + "/api/v1/plugins/import?" + params, {
      headers, data: zip,
    });
    expect(installed.ok(), await installed.text()).toBe(true);
    const row = await installed.json();
    const catalog = (await (await page.request.get(appUrl + "/api/v1/plugins")).json()).catalog_revision;
    const configured = await page.request.put(appUrl + "/api/v1/plugins/" + pluginId, {
      headers,
      data: {
        catalog_revision: catalog,
        expected_revision: row.revision,
        approved_sha256: row.payload.package_sha256,
        enabled: true,
        grants: [{ capability: "network.request", scopes: [url] }],
        trusted_full_access_ack: false,
        confirmed: true,
      },
    });
    expect(configured.ok(), await configured.text()).toBe(true);
    expect(count).toBe(0);
    await page.getByRole("button", { name: "Расширения", exact: true }).click();
    const manager = page.getByLabel("Управление расширениями", { exact: true });
    await manager.getByRole("button", { name: "Обновить каталог расширений" }).click();
    await manager.getByRole("button", { name: "Локальная сеть · 1.0.0" }).click();
    const runner = manager.getByLabel("Запуск выбранного расширения", { exact: true });
    await runner.getByRole("button", { name: "Вычислить план расширения" }).click();
    const preview = runner.getByRole("region", { name: "Предпросмотр сетевого запроса" });
    await expect(preview).toBeVisible({ timeout: 20000 });
    await expect(preview).toContainText(url);
    expect(count).toBe(0);
    const article = runner.getByRole("article", { name: /План расширения/ });
    await article.getByLabel(
      "Подтверждаю локальный сетевой запрос и риск внешнего необратимого действия",
    ).check();
    await article.getByRole("button", { name: "Выполнить подтверждённый локальный сетевой запрос" }).click();
    await expect.poll(async () => {
      const data = await (
        await page.request.get(appUrl + "/api/v1/plugins/" + pluginId + "/previews")
      ).json();
      return data.items[0]?.payload.status;
    }, { timeout: 20000 }).toBe("observed");
    expect(count).toBe(1);
    const data = await (
      await page.request.get(appUrl + "/api/v1/plugins/" + pluginId + "/previews")
    ).json();
    const receipt = data.items[0];
    expect(receipt.payload.observed.status_code).toBe(200);
    const retry = await page.request.post(
      appUrl + "/api/v1/plugins/previews/" + receipt.id + "/execute-network",
      {
        headers,
        data: {
          expected_revision: 1,
          confirmed: true,
          full_access_warning_ack: true,
          fingerprint: receipt.payload.metadata.external_network.fingerprint,
        },
      },
    );
    expect(retry.ok(), await retry.text()).toBe(true);
    expect(count).toBe(1);
  } finally {
    await new Promise<void>((done) => server.close(() => done()));
  }
});
