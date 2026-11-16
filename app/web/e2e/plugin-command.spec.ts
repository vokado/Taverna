import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

test("Внешняя команда: отдельное подтверждение, точный EXE, одна операция без повтора", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(120000);
  const folder = mkdtempSync(join(tmpdir(), "taverna-command-e2e-"));
  const marker = join(folder, "effects.txt");
  const python = process.env.TAVERNA_PYTHON || "python";
  const executable = execFileSync(python, ["-c", "import sys,pathlib;print(pathlib.Path(sys.executable).resolve())"])
    .toString().trim();
  const sha256 = createHash("sha256").update(readFileSync(executable)).digest("hex");
  const script = `from pathlib import Path;p=Path(${JSON.stringify(marker)});p.write_text((p.read_text() if p.exists() else "")+"X")`;
  const pluginId = crypto.randomUUID();
  const plan = {
    format: 1,
    message: "Проверить внешнюю программу",
    actions: [
      {
        type: "command.exec",
        executable,
        sha256,
        argv: ["-c", script],
        timeout_seconds: 5,
      },
    ],
  };
  const manifest = {
    format: 1,
    id: pluginId,
    name: "Команда с подтверждением",
    version: "1.0.0",
    api_version: 1,
    runtime: "declarative",
    entry: "main.json",
    requests: [
      {
        capability: "commands.exec",
        reason: "Точный ручной запуск",
        scopes: [executable],
      },
    ],
  };
  const zip = execFileSync(python, [resolve("e2e/support/plugin_fixture.py")], {
    input: JSON.stringify({ manifest, source: JSON.stringify(plan) }),
  });
  try {
    const csrf = (await (await page.request.get(appUrl + "/api/v1/session")).json()).csrf;
    const headers = { "X-Taverna-CSRF": csrf };
    const inspected = await page.request.post(appUrl + "/api/v1/plugins/import-preview", {
      headers,
      data: zip,
    });
    expect(inspected.ok(), await inspected.text()).toBe(true);
    const preview = await inspected.json();
    const query = new URLSearchParams({
      expected_revision: String(preview.expected_revision),
      catalog_revision: String(preview.catalog_revision),
      approved_sha256: preview.sha256,
      confirmed: "true",
    });
    const created = await page.request.post(appUrl + "/api/v1/plugins/import?" + query, {
      headers,
      data: zip,
    });
    expect(created.ok(), await created.text()).toBe(true);
    const row = await created.json();
    const catalog = (await (await page.request.get(appUrl + "/api/v1/plugins")).json()).catalog_revision;
    const configured = await page.request.put(appUrl + "/api/v1/plugins/" + pluginId, {
      headers,
      data: {
        catalog_revision: catalog,
        expected_revision: row.revision,
        approved_sha256: row.payload.package_sha256,
        enabled: true,
        grants: [
          {
            capability: "commands.exec",
            scopes: [executable],
            full_access_warning_ack: true,
          },
        ],
        trusted_full_access_ack: false,
        confirmed: true,
      },
    });
    expect(configured.ok(), await configured.text()).toBe(true);
    expect(existsSync(marker)).toBe(false);

    await page.getByRole("button", { name: "Расширения", exact: true }).click();
    const manager = page.getByLabel("Управление расширениями", { exact: true });
    await manager.getByRole("button", { name: "Обновить каталог расширений" }).click();
    await manager.getByRole("button", { name: "Команда с подтверждением · 1.0.0" }).click();
    const runner = manager.getByLabel("Запуск выбранного расширения", { exact: true });
    await runner.getByRole("button", { name: "Вычислить план расширения" }).click();
    const commandView = runner.getByRole("region", {
      name: "Предпросмотр внешней команды",
    });
    await expect(commandView).toBeVisible({ timeout: 20000 });
    await expect(commandView).toContainText(executable);
    await expect(commandView).toContainText(sha256);
    expect(existsSync(marker)).toBe(false);

    const actions = runner.getByRole("article", { name: /План расширения/ });
    await actions.getByLabel(
      "Подтверждаю запуск внешней программы с полным доступом и риск необратимых действий",
    ).check();
    await actions.getByRole("button", { name: "Запустить проверенную внешнюю команду" }).click();

    await expect
      .poll(async () => {
        const data = await (
          await page.request.get(appUrl + "/api/v1/plugins/" + pluginId + "/previews")
        ).json();
        return data.items[0]?.payload.status;
      }, { timeout: 20000 })
      .toBe("observed");
    expect(readFileSync(marker, "utf8")).toBe("X");

    const data = await (
      await page.request.get(appUrl + "/api/v1/plugins/" + pluginId + "/previews")
    ).json();
    const commandRecord = data.items[0];
    const again = await page.request.post(
      appUrl + "/api/v1/plugins/previews/" + commandRecord.id + "/execute-command",
      {
        headers,
        data: {
          expected_revision: 1,
          confirmed: true,
          full_access_warning_ack: true,
          fingerprint: commandRecord.payload.metadata.external_command.fingerprint,
        },
      },
    );
    expect(again.ok(), await again.text()).toBe(true);
    expect(readFileSync(marker, "utf8")).toBe("X");
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});
