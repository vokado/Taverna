import { test as base, expect } from "@playwright/test";
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";

export const test = base.extend<{ appUrl: string }>({
  appUrl: async ({ page }, use) => {
    const root = mkdtempSync(join(tmpdir(), "taverna-user-flow-"));
    mkdirSync(join(root, "app/web"), { recursive: true });
    cpSync(resolve("dist"), join(root, "app/web/dist"), { recursive: true });
    const process = spawn(
      globalThis.process.env.TAVERNA_PYTHON || "python",
      ["-m", "taverna.launcher", "--root", root, "--no-browser"],
      { stdio: "ignore" },
    );
    let url = "";
    try {
      await expect
        .poll(async () => {
          try {
            const info = JSON.parse(
              readFileSync(join(root, "data/config/instance.json"), "utf8"),
            );
            url = `http://127.0.0.1:${info.port}`;
            return (await fetch(url + "/api/v1/health")).status;
          } catch {
            return 0;
          }
        })
        .toBe(200);
      const key = readFileSync(join(root, "data/config/launcher.key"), "utf8");
      const response = await fetch(url + "/api/v1/launcher/open", {
        method: "POST",
        headers: { "X-Taverna-Launcher": key },
      });
      await page.goto((await response.json()).url);
      await expect(
        page.getByRole("button", { name: "Завершить приложение" }),
      ).toBeVisible();
      await use(url);
    } finally {
      if (process.exitCode === null) {
        try {
          const session = await page.request.get(url + "/api/v1/session");
          const csrf = (await session.json()).csrf;
          await page.request.post(url + "/api/v1/system/shutdown", {
            headers: { "X-Taverna-CSRF": csrf },
          });
          await expect
            .poll(() => process.exitCode, { timeout: 10000 })
            .not.toBeNull();
        } catch {
          process.kill();
        }
      }
      if (process.exitCode === null)
        await new Promise((resolve) => process.once("exit", resolve));
      rmSync(root, { recursive: true, force: true });
    }
  },
});
export { expect };
