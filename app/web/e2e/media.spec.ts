import { test, expect } from "./fixtures";
import { resolve } from "node:path";

test("Медиа: импорт, теги, сцена, эмоция, настоящее аудио и независимое отключение", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  await page
    .getByRole("button", { name: "Медиабиблиотека", exact: true })
    .click();
  const library = page.getByLabel("Медиабиблиотека", { exact: true });
  await library.getByText("Импорт собственного медиа", { exact: true }).click();
  async function importFile(file: string, name: string, category: string) {
    await library
      .getByLabel("Медиафайл", { exact: true })
      .setInputFiles(resolve("../../tests/fixtures/media/" + file));
    await library
      .getByRole("textbox", {
        name: "Название импортируемого медиа",
        exact: true,
      })
      .fill(name);
    await library
      .getByRole("combobox", {
        name: "Категория импортируемого медиа",
        exact: true,
      })
      .selectOption(category);
    await library
      .getByRole("button", { name: "Подтвердить импорт медиа", exact: true })
      .click();
    await expect(library.getByRole("status")).toContainText(
      "Медиа импортировано и проверено",
    );
  }
  await importFile("background.png", "Гавань", "background");
  const tags = library.getByRole("textbox", {
    name: "Теги медиаресурса через запятую",
    exact: true,
  });
  await tags.pressSequentially("море, ночь");
  await expect(tags).toHaveValue("море, ночь");
  await library
    .getByRole("button", { name: "Сохранить метаданные медиа", exact: true })
    .click();
  await expect(library.getByRole("status")).toContainText(
    "Метаданные медиа сохранены",
  );
  await importFile("expression.webp", "Улыбка Миры", "expression");
  await importFile("tone.wav", "Тема гавани", "music");
  const csrf = (
      await (await page.request.get(appUrl + "/api/v1/session")).json()
    ).csrf,
    headers = { "X-Taverna-CSRF": csrf };
  const card = (
    await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
  ).character;
  card.fields.name = "Мира";
  const cid = (
    await (
      await page.request.post(appUrl + "/api/v1/characters", {
        headers,
        data: { expected_revision: 0, character: card },
      })
    ).json()
  ).id;
  const sid = (
    await (
      await page.request.post(appUrl + "/api/v1/stories", {
        headers,
        data: { name: "Медиасцена гавани", character_ids: [cid] },
      })
    ).json()
  ).id;
  let bundle = await (
    await page.request.get(appUrl + `/api/v1/stories/${sid}`)
  ).json();
  await page.request.post(appUrl + `/api/v1/stories/${sid}/messages`, {
    headers,
    data: {
      branch_id: bundle.branch.id,
      expected_revision: bundle.branch.revision,
      text: "История остаётся литературной",
    },
  });
  const before = await (
    await page.request.get(appUrl + `/api/v1/stories/${sid}/messages`)
  ).json();
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("button", { name: "Обновить истории", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(sid);
  const studio = page.getByLabel("Студия сцены", { exact: true });
  await studio.getByText("Студия сцены и атмосфера", { exact: true }).click();
  await studio.getByText("Визуальные слои", { exact: true }).click();
  await studio
    .getByRole("combobox", { name: "Изображение нового слоя", exact: true })
    .selectOption({ label: "Гавань" });
  await studio
    .getByRole("button", { name: "Добавить слой сцены", exact: true })
    .click();
  await studio
    .getByRole("textbox", { name: "Название слоя", exact: true })
    .fill("Фон гавани");
  await studio
    .getByRole("button", { name: "Добавить слой сцены", exact: true })
    .click();
  await studio
    .getByRole("textbox", { name: "Название слоя", exact: true })
    .fill("Мира в сцене");
  await studio
    .getByRole("combobox", { name: "NPC слоя", exact: true })
    .selectOption(cid);
  await studio
    .getByRole("spinbutton", { name: "Ширина слоя, %", exact: true })
    .fill("35");
  await studio.getByText("Варианты внешности NPC", { exact: true }).click();
  await studio
    .getByRole("combobox", { name: "Изображение варианта", exact: true })
    .selectOption({ label: "Улыбка Миры" });
  await studio
    .getByRole("combobox", { name: "Эмоция варианта", exact: true })
    .selectOption("спокойствие");
  await studio
    .getByRole("button", { name: "Добавить вариант внешности", exact: true })
    .click();
  await studio.getByText("Музыка и звуки окружения", { exact: true }).click();
  await studio
    .getByRole("combobox", { name: "Музыка сцены", exact: true })
    .selectOption({ label: "Тема гавани" });
  await studio
    .getByRole("checkbox", { name: "Включить музыку", exact: true })
    .check();
  await studio
    .getByRole("spinbutton", { name: "Громкость музыки (0…1)", exact: true })
    .fill("0.2");
  await studio
    .getByRole("combobox", { name: "Эффект сцены", exact: true })
    .selectOption("rain");
  await studio
    .getByRole("button", { name: "Сохранить студию сцены", exact: true })
    .click();
  await expect(studio.getByRole("status")).toContainText(
    "Композиция и привязки сцены сохранены",
  );
  const stage = page.getByLabel("Медиасцена", { exact: true });
  await expect(stage.locator(".scene-layer")).toHaveCount(2);
  const layer = stage.getByRole("img", { name: "Мира в сцене", exact: true });
  await expect
    .poll(() => layer.evaluate((e: HTMLImageElement) => e.naturalWidth))
    .toBeGreaterThan(0);
  const imageData = await (
    await page.request.get(
      appUrl + "/api/v1/media?query=" + encodeURIComponent("Улыбка Миры"),
    )
  ).json();
  await expect(layer).toHaveAttribute(
    "src",
    `/api/v1/media/${imageData.items[0].id}/file`,
  );
  await stage
    .getByRole("button", { name: "Воспроизвести музыку", exact: true })
    .click();
  await expect
    .poll(() =>
      stage
        .locator("audio")
        .evaluate((e: HTMLAudioElement) => !e.paused && e.readyState >= 2),
    )
    .toBe(true);
  expect(
    await stage.locator("audio").evaluate((e: HTMLAudioElement) => e.volume),
  ).toBe(0.2);
  const leavingAudio = await stage.locator("audio").elementHandle();
  await page
    .getByRole("button", { name: "Медиабиблиотека", exact: true })
    .click();
  await expect
    .poll(() => leavingAudio!.evaluate((e: HTMLAudioElement) => e.paused))
    .toBe(true);
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await stage
    .getByRole("button", { name: "Воспроизвести музыку", exact: true })
    .click();
  await expect
    .poll(() =>
      stage.locator("audio").evaluate((e: HTMLAudioElement) => !e.paused),
    )
    .toBe(true);
  const stoppingAudio = await stage.locator("audio").elementHandle();
  await studio
    .getByRole("checkbox", {
      name: "Уменьшить эффекты этой сцены",
      exact: true,
    })
    .check();
  await studio
    .getByRole("button", { name: "Сохранить студию сцены", exact: true })
    .click();
  await expect(
    stage.getByLabel("Сцена с уменьшенными эффектами", { exact: true }),
  ).toBeVisible();
  await expect(layer).toHaveAttribute(
    "src",
    `/api/v1/media/${imageData.items[0].id}/thumbnail`,
  );
  await expect(stage.locator(".scene-effect")).toHaveCount(0);
  await studio
    .getByRole("checkbox", { name: "Включить медиа сцены", exact: true })
    .uncheck();
  await studio
    .getByRole("button", { name: "Сохранить студию сцены", exact: true })
    .click();
  await expect(stage).toHaveCount(0);
  await expect
    .poll(() => stoppingAudio!.evaluate((e: HTMLAudioElement) => e.paused))
    .toBe(true);
  const after = await (
    await page.request.get(appUrl + `/api/v1/stories/${sid}/messages`)
  ).json();
  expect(after).toEqual(before);
  await studio
    .getByText("JSON и черновики студии сцены", { exact: true })
    .click();
  await studio
    .getByRole("textbox", { name: "JSON студии сцены", exact: true })
    .fill("{незавершённая сцена");
  await studio
    .getByRole("button", { name: "Проверить JSON сцены", exact: true })
    .click();
  await expect(studio.getByRole("status")).toContainText(
    "Черновик сцены автосохранён",
  );
  page.on("dialog", (d) => d.accept());
  await page.reload();
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(sid);
  await studio.getByText("Студия сцены и атмосфера", { exact: true }).click();
  await studio
    .getByText("JSON и черновики студии сцены", { exact: true })
    .click();
  await studio
    .getByRole("button", { name: /Восстановить черновик сцены / })
    .first()
    .click();
  await expect(
    studio.getByRole("textbox", { name: "JSON студии сцены", exact: true }),
  ).toHaveValue("{незавершённая сцена");
});
