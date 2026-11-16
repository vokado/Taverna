import { test as base, expect } from "@playwright/test";
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";

export const test = base.extend<{ appUrl: string }>({
  appUrl: [
    async ({ page }, use, testInfo) => {
      const root = mkdtempSync(join(tmpdir(), "taverna-user-flow-"));
      mkdirSync(join(root, "app/web"), { recursive: true });
      cpSync(resolve("dist"), join(root, "app/web/dist"), { recursive: true });
      const process = spawn(
        globalThis.process.env.TAVERNA_PYTHON || "python",
        ["-m", "taverna.launcher", "--root", root, "--no-browser"],
        { stdio: "ignore" },
      );
      let url = "";
      let spawnError: string | undefined;
      process.on("error", (error: NodeJS.ErrnoException) => {
        spawnError = error.code || error.name;
      });
      const started = performance.now();
      let healthStatus = 0;
      let attempts = 0;
      let ready = false;
      try {
        // Cold startup includes imports and first-run JSON setup on Windows CI.
        // A longer deadline must not hide a process crash.
        while (performance.now() - started < 30000) {
          if (
            spawnError ||
            process.exitCode !== null ||
            process.signalCode !== null
          )
            throw Error(
              `Application exited before readiness (${spawnError || process.exitCode || process.signalCode})`,
            );
          attempts++;
          try {
            const info = JSON.parse(
              readFileSync(join(root, "data/config/instance.json"), "utf8"),
            );
            url = `http://127.0.0.1:${info.port}`;
            healthStatus = (
              await fetch(url + "/api/v1/health", {
                signal: AbortSignal.timeout(1000),
              })
            ).status;
          } catch {
            healthStatus = 0;
          }
          if (healthStatus === 200) {
            ready = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        expect(
          ready,
          "Application health must become ready within 30 seconds",
        ).toBe(true);
        const key = readFileSync(
          join(root, "data/config/launcher.key"),
          "utf8",
        );
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
        if (!ready) {
          // No bootstrap URL, launcher key, session cookies or raw headers.
          await testInfo.attach("startup-diagnostics.json", {
            body: JSON.stringify({
              elapsed_ms: performance.now() - started,
              attempts,
              health_status: healthStatus,
              exit_code: process.exitCode,
              signal: process.signalCode,
              spawn_error: spawnError,
            }),
            contentType: "application/json",
          });
        }
        if (
          process.exitCode === null &&
          process.signalCode === null &&
          !spawnError
        ) {
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
        if (
          process.exitCode === null &&
          process.signalCode === null &&
          !spawnError
        )
          await new Promise((resolve) => process.once("exit", resolve));
        rmSync(root, { recursive: true, force: true });
      }
    },
    { timeout: 45000 },
  ],
});
export { expect };
