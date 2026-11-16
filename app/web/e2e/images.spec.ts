import { test, expect } from "./fixtures";
import { spawn } from "node:child_process";
import { resolve } from "node:path";

test("Изображения: реальный HTTP-контракт, галерея, адресная отмена и черновик без автозапуска", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  const engine = spawn(
    process.env.TAVERNA_PYTHON || "python",
    [
      resolve("e2e/support/comfy_fixture.py"),
      "--image",
      resolve("../../tests/fixtures/media/generated.png"),
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  try {
    let output = "",
      stderr = "";
    const endpoint = await new Promise<string>((done, fail) => {
      const timeout = setTimeout(
        () => fail(Error("Test HTTP engine did not start")),
        10000,
      );
      engine.stdout.on("data", (data) => {
        output += data.toString();
        const newline = output.indexOf("\n");
        if (newline >= 0) {
          clearTimeout(timeout);
          done(`http://127.0.0.1:${JSON.parse(output.slice(0, newline)).port}`);
        }
      });
      engine.stderr.on("data", (data) => {
        stderr += data.toString();
      });
      engine.on("error", (error) => {
        clearTimeout(timeout);
        fail(error);
      });
      engine.once("exit", (code) => {
        if (!output.includes("\n")) {
          clearTimeout(timeout);
          fail(Error(`Test engine exited ${code}: ${stderr}`));
        }
      });
    });
    await page
      .getByRole("button", { name: "Медиабиблиотека", exact: true })
      .click();
    const panel = page.getByLabel("Локальная генерация изображений", {
      exact: true,
    });
    await panel
      .getByText("Локальная генерация изображений", { exact: true })
      .click();
    await panel
      .getByText("Подключение и ограничения генератора", { exact: true })
      .click();
    await panel
      .getByRole("checkbox", {
        name: "Разрешить локальную генерацию изображений",
        exact: true,
      })
      .check();
    await panel
      .getByRole("textbox", {
        name: "Адрес генератора изображений",
        exact: true,
      })
      .fill(endpoint);
    await panel
      .getByRole("button", {
        name: "Сохранить настройки генератора",
        exact: true,
      })
      .click();
    await expect(panel.getByRole("status")).toContainText(
      "Настройки генератора сохранены",
    );
    await panel
      .getByRole("button", {
        name: "Проверить локальный генератор",
        exact: true,
      })
      .click();
    await expect(panel.getByRole("status")).toContainText(
      "API движка и адресная отмена доступны",
    );
    expect(
      (await (await page.request.get(endpoint + "/test/stats")).json()).prompts,
    ).toBe(0);
    await panel
      .getByRole("textbox", {
        name: "Название результата изображения",
        exact: true,
      })
      .fill("Синяя гавань");
    await panel
      .getByRole("textbox", { name: "Запрос изображения", exact: true })
      .fill("A harbor at night, an illustrated scene");
    await panel
      .getByRole("button", { name: "Сгенерировать изображение", exact: true })
      .click();
    const result = panel.getByRole("article", {
      name: "Результат генерации изображения",
      exact: true,
    });
    await expect(result).toBeVisible({ timeout: 15000 });
    await expect
      .poll(() =>
        result
          .getByRole("img")
          .evaluate((e: HTMLImageElement) => e.naturalWidth),
      )
      .toBe(512);
    await panel
      .getByRole("button", { name: "Открыть результат в галерее", exact: true })
      .click();
    await expect(
      page
        .getByLabel("Редактор медиаресурса", { exact: true })
        .getByRole("textbox", { name: "Название медиаресурса", exact: true }),
    ).toHaveValue("Синяя гавань");
    await page.request.post(endpoint + "/test/config", {
      data: { hold: true },
    });
    await panel
      .getByRole("textbox", {
        name: "Название результата изображения",
        exact: true,
      })
      .fill("Отменённая гавань");
    await panel
      .getByRole("button", { name: "Сгенерировать изображение", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(endpoint + "/test/stats")).json())
            .prompts,
        { timeout: 20000 },
      )
      .toBe(2);
    await panel
      .getByRole("button", {
        name: "Остановить генерацию изображения",
        exact: true,
      })
      .click();
    await expect(
      panel.getByText("Задача изображения: Отменена.", { exact: true }),
    ).toBeVisible({ timeout: 15000 });
    const stats = await (
      await page.request.get(endpoint + "/test/stats")
    ).json();
    expect(stats.cancels).toBe(1);
    expect(stats.global_interrupts).toBe(0);
    expect(stats.noop_cancels).toBeGreaterThan(0);
    const assets = await (
      await page.request.get(appUrl + "/api/v1/media?limit=500")
    ).json();
    expect(assets.total).toBe(1);
    expect(assets.items[0].payload.provenance.source).toBe("comfyui-modern-v1");
    await panel
      .getByRole("spinbutton", { name: "Ширина изображения", exact: true })
      .fill("513.5");
    await expect(panel.getByRole("status")).toContainText(
      "Черновик изображения автосохранён",
    );
    page.on("dialog", (d) => d.accept());
    await page.reload();
    await page
      .getByRole("button", { name: "Медиабиблиотека", exact: true })
      .click();
    await panel
      .getByText("Локальная генерация изображений", { exact: true })
      .click();
    await panel
      .getByText("Черновики генерации изображений", { exact: true })
      .click();
    await panel
      .getByRole("button", { name: /Восстановить черновик изображения / })
      .first()
      .click();
    await expect(
      panel.getByRole("textbox", { name: "Запрос изображения", exact: true }),
    ).toHaveValue("A harbor at night, an illustrated scene");
    await expect(
      panel.getByRole("spinbutton", {
        name: "Ширина изображения",
        exact: true,
      }),
    ).toHaveValue("513.5");
    expect(
      (await (await page.request.get(endpoint + "/test/stats")).json()).prompts,
    ).toBe(2);
    await expect(
      panel.getByRole("button", {
        name: "Сгенерировать изображение",
        exact: true,
      }),
    ).toBeDisabled();
  } finally {
    if (engine.pid && engine.exitCode === null && engine.signalCode === null) {
      const stopped = new Promise<void>((done) =>
        engine.once("exit", () => done()),
      );
      engine.kill();
      await stopped;
    }
  }
});
