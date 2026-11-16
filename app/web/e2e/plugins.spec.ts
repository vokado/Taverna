import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { mkdtempSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

test("Менеджер расширений: ZIP без autorun, отдельное доверие и права, исходник как текст, черновик и удаление", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  const folder = mkdtempSync(join(tmpdir(), "taverna-plugin-fixture-")),
    marker = join(folder, "must-not-run");
  const manifest = {
    format: 1,
    id: crypto.randomUUID(),
    name: "Проверка доверия",
    version: "1.0.0",
    api_version: 1,
    runtime: "trusted_python",
    entry: "main.py",
    requests: [
      {
        capability: "notes.write",
        reason: "Создание заметки по подтверждённому плану",
        scopes: ["new"],
      },
    ],
  };
  const source = `from pathlib import Path\nPath(${JSON.stringify(marker)}).write_text("executed")\n# <script id="injected">bad()</script>\ndef run(request): return {"actions": [],"message":"source fixture"}\n`;
  const zip = execFileSync(
    process.env.TAVERNA_PYTHON || "python",
    [resolve("e2e/support/plugin_fixture.py")],
    { input: JSON.stringify({ manifest, source }) },
  );
  try {
    await page.getByRole("button", { name: "Расширения", exact: true }).click();
    const manager = page.getByLabel("Управление расширениями", { exact: true });
    await manager
      .getByLabel("Пакет расширения", { exact: true })
      .setInputFiles({
        name: "Проверяемый.zip",
        mimeType: "application/zip",
        buffer: zip,
      });
    await manager
      .getByRole("button", { name: "Проверить пакет без запуска", exact: true })
      .click();
    const preview = manager.getByRole("article", {
      name: "Предпросмотр пакета расширения",
      exact: true,
    });
    await expect(preview).toBeVisible();
    await expect(preview).toContainText("полный доступ");
    await expect(
      manager.getByRole("button", {
        name: "Подтвердить установку расширения",
        exact: true,
      }),
    ).toBeDisabled();
    expect(existsSync(marker)).toBe(false);
    await manager
      .getByLabel(
        "Подтверждаю установку просмотренного пакета без выдачи прав и без запуска",
        { exact: true },
      )
      .check();
    await manager
      .getByRole("button", {
        name: "Подтвердить установку расширения",
        exact: true,
      })
      .click();
    await expect(manager.getByRole("status")).toContainText(
      "Код не запускался",
    );
    const stored = await (
      await page.request.get(appUrl + "/api/v1/plugins/" + manifest.id)
    ).json();
    expect(stored.payload.enabled).toBe(false);
    expect(stored.payload.grants).toEqual([]);
    await manager
      .getByLabel("Разрешить последующий явный запуск расширения", {
        exact: true,
      })
      .check();
    await manager
      .getByLabel("Выдать: Изменение заметок", { exact: true })
      .check();
    await manager
      .getByLabel("Разрешённые области: Изменение заметок", { exact: true })
      .fill("new");
    await manager
      .getByLabel("Подтверждаю изменение состояния и перечисленных прав", {
        exact: true,
      })
      .check();
    await manager
      .getByRole("button", { name: "Сохранить состояние и права", exact: true })
      .click();
    await expect(manager.getByRole("status")).toContainText("доверие");
    expect(
      (
        await (
          await page.request.get(appUrl + "/api/v1/plugins/" + manifest.id)
        ).json()
      ).payload.enabled,
    ).toBe(false);
    await manager
      .getByLabel(
        "Я доверяю именно этому SHA256 и понимаю полный доступ Python",
        { exact: true },
      )
      .check();
    await manager
      .getByLabel("Подтверждаю изменение состояния и перечисленных прав", {
        exact: true,
      })
      .check();
    await manager
      .getByRole("button", { name: "Сохранить состояние и права", exact: true })
      .click();
    await expect(manager.getByRole("status")).toContainText("права сохранены");
    const enabled = await (
      await page.request.get(appUrl + "/api/v1/plugins/" + manifest.id)
    ).json();
    expect(enabled.payload.enabled).toBe(true);
    expect(enabled.payload.grants).toHaveLength(1);
    expect(enabled.payload.trusted_full_access_ack).toBe(true);
    expect(existsSync(marker)).toBe(false);
    await manager
      .getByRole("button", { name: "Просмотреть исходник entry", exact: true })
      .click();
    await expect(
      manager
        .getByRole("article", { name: "Исходник расширения", exact: true })
        .locator("pre"),
    ).toContainText('<script id="injected">');
    expect(await page.locator("script#injected").count()).toBe(0);
    await manager
      .getByLabel("Выдать: Изменение заметок", { exact: true })
      .uncheck();
    await manager
      .getByLabel("Подтверждаю изменение состояния и перечисленных прав", {
        exact: true,
      })
      .check();
    await manager
      .getByRole("button", { name: "Сохранить состояние и права", exact: true })
      .click();
    await expect(manager.getByRole("status")).toContainText("права сохранены");
    const revoked = await (
      await page.request.get(appUrl + "/api/v1/plugins/" + manifest.id)
    ).json();
    expect(revoked.payload.grants).toEqual([]);
    expect(revoked.payload.permission_epoch).toBeGreaterThan(
      enabled.payload.permission_epoch,
    );
    await manager
      .getByText("JSON разрешений расширения", { exact: true })
      .click();
    await manager
      .getByLabel("JSON разрешений расширения", { exact: true })
      .fill('{"grants": [');
    await expect(
      manager.getByText("Черновик разрешений расширения автосохранён.", {
        exact: true,
      }),
    ).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Расширения", exact: true }).click();
    await manager
      .getByText("Черновики разрешений расширений", { exact: true })
      .click();
    await manager
      .getByRole("button", { name: /Восстановить права / })
      .first()
      .click();
    await manager
      .getByText("JSON разрешений расширения", { exact: true })
      .click();
    await expect(
      manager.getByLabel("JSON разрешений расширения", { exact: true }),
    ).toHaveValue('{"grants": [');
    await manager.getByText("Удаление пакета", { exact: true }).click();
    await manager
      .getByLabel(
        "Подтверждаю удаление выбранного расширения и отключение зависимых",
        { exact: true },
      )
      .check();
    await manager
      .getByRole("button", { name: "Удалить расширение", exact: true })
      .click();
    await expect(manager.getByRole("status")).toContainText(
      "файлы пакета убраны",
    );
    expect(
      (await (await page.request.get(appUrl + "/api/v1/plugins")).json()).items,
    ).toEqual([]);
    expect(existsSync(marker)).toBe(false);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});
