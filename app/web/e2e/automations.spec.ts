import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

test("Правила: UI сохраняет макрос, запуск создаёт план без применения", async ({ page, appUrl }) => {
  test.setTimeout(90000);
  const python = process.env.TAVERNA_PYTHON || "python";
  const pluginId = crypto.randomUUID();
  const manifest = {
    format: 1, id: pluginId, name: "Проверочный макрос", version: "1.0.0",
    api_version: 1, runtime: "declarative", entry: "main.json",
    requests: [{ capability: "own.write", reason: "Точная запись", scopes: ["memo"] }],
  };
  const plan = {
    format: 1, message: "Проверка",
    actions: [{ type: "own.set", target: "memo", expected_revision: 0, value: { text: "Только после подтверждения" } }],
  };
  const zip = execFileSync(python, [resolve("e2e/support/plugin_fixture.py")], {
    input: JSON.stringify({ manifest, source: JSON.stringify(plan) }),
  });
  const csrf = (await (await page.request.get(appUrl + "/api/v1/session")).json()).csrf;
  const headers = { "X-Taverna-CSRF": csrf };
  const inspected = await page.request.post(appUrl + "/api/v1/plugins/import-preview", { headers, data: zip });
  expect(inspected.ok(), await inspected.text()).toBe(true);
  const p = await inspected.json();
  const query = new URLSearchParams({
    expected_revision: String(p.expected_revision), catalog_revision: String(p.catalog_revision),
    approved_sha256: p.sha256, confirmed: "true",
  });
  const installed = await page.request.post(appUrl + "/api/v1/plugins/import?" + query, { headers, data: zip });
  expect(installed.ok(), await installed.text()).toBe(true);
  const row = await installed.json();
  const registry = await (await page.request.get(appUrl + "/api/v1/plugins")).json();
  const configured = await page.request.put(appUrl + "/api/v1/plugins/" + pluginId, {
    headers,
    data: {
      catalog_revision: registry.catalog_revision, expected_revision: row.revision,
      approved_sha256: row.payload.package_sha256, enabled: true,
      grants: [{ capability: "own.write", scopes: ["memo"] }],
      trusted_full_access_ack: false, confirmed: true,
    },
  });
  expect(configured.ok(), await configured.text()).toBe(true);

  await page.getByRole("button", { name: "Автоматизация", exact: true }).click();
  const panel = page.getByLabel("Правила и расписания", { exact: true });
  await panel.getByLabel("Название", { exact: true }).fill("Макрос для проверки");
  await panel.getByLabel("Расширение для правила").selectOption(pluginId);
  await panel.getByLabel("Подтверждаю привязку к текущей версии расширения и его разрешениям").check();
  await panel.getByRole("button", { name: "Сохранить правило" }).click();
  const rule = panel.getByRole("article", { name: "Правило Макрос для проверки" });
  await expect(rule).toBeVisible();
  await rule.getByRole("button", { name: "Запустить макрос" }).click();
  await expect(panel.getByRole("status")).toContainText("Макрос поставлен в очередь");
  await expect.poll(async () => {
    const data = await (await page.request.get(appUrl + "/api/v1/plugins/" + pluginId + "/previews")).json();
    return data.items.length;
  }, { timeout: 20000 }).toBe(1);
  const data = await (await page.request.get(appUrl + "/api/v1/plugins/" + pluginId + "/previews")).json();
  expect(data.items[0].payload.status).toBe("pending");
  expect(data.items[0].payload.plan.actions[0].value.text).toBe("Только после подтверждения");
});
