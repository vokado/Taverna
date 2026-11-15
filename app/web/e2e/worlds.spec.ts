import { test, expect } from "./fixtures";
import { readFileSync } from "node:fs";
test("Мир, сценарий, снимок истории и время; невалидный JSON и черновик", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  await page
    .getByRole("button", { name: "Миры и сценарии", exact: true })
    .click();
  const world = page.getByRole("article", {
    name: "Конструктор мира",
    exact: true,
  });
  await world
    .getByRole("textbox", { name: "Название шаблона", exact: true })
    .fill("Мир архипелага");
  await world
    .getByRole("button", { name: "Добавить сущность", exact: true })
    .click();
  await world
    .getByRole("textbox", { name: "Название сущности", exact: true })
    .fill("Гавань");
  const harbor = await world
    .getByRole("combobox", { name: "Сущность мира", exact: true })
    .inputValue();
  await world.getByText("Связи сущностей и ресурсы", { exact: true }).click();
  await world
    .getByRole("textbox", { name: "Ключ ресурса", exact: true })
    .fill("зерно");
  await world
    .getByRole("spinbutton", {
      name: "Начальное количество ресурса",
      exact: true,
    })
    .fill("10");
  await world
    .getByRole("button", { name: "Установить ресурс", exact: true })
    .click();
  await world.getByText("Правила событий", { exact: true }).click();
  await world
    .getByRole("combobox", { name: "Тип последствия", exact: true })
    .selectOption("change_resource");
  await world
    .getByRole("textbox", { name: "Ключ последствия", exact: true })
    .fill("зерно");
  await world
    .getByRole("textbox", { name: "Значение последствия JSON", exact: true })
    .fill("-2");
  await world
    .getByRole("button", { name: "Добавить проверенное правило", exact: true })
    .click();
  await expect(world.getByText("Правило 1", { exact: true })).toBeVisible();
  await world
    .getByRole("button", { name: "Сохранить шаблон", exact: true })
    .click();
  await expect(world.getByRole("status")).toContainText("Шаблон сохранён.");
  const wid = await world
    .getByRole("combobox", { name: "Сохранённый шаблон", exact: true })
    .inputValue();
  await page
    .getByRole("combobox", { name: "Тип конструктора", exact: true })
    .selectOption("scenarios");
  const scenario = page.getByRole("article", {
    name: "Конструктор сценария",
    exact: true,
  });
  await scenario
    .getByRole("button", { name: "Обновить шаблоны", exact: true })
    .click();
  await scenario
    .getByRole("textbox", { name: "Название шаблона", exact: true })
    .fill("Прибытие");
  await scenario
    .getByRole("combobox", { name: "Мир сценария", exact: true })
    .selectOption(wid);
  await scenario
    .getByRole("button", { name: "Добавить начало", exact: true })
    .click();
  await scenario
    .getByRole("textbox", { name: "Название начала", exact: true })
    .fill("У причала");
  await scenario
    .getByRole("combobox", { name: "Начальная локация", exact: true })
    .selectOption(harbor);
  await scenario
    .getByRole("button", { name: "Сохранить шаблон", exact: true })
    .click();
  await expect(scenario.getByRole("status")).toContainText("Шаблон сохранён.");
  const scid = await scenario
    .getByRole("combobox", { name: "Сохранённый шаблон", exact: true })
    .inputValue();
  const csrf = (
    await (await page.request.get(appUrl + "/api/v1/session")).json()
  ).csrf;
  const headers = { "X-Taverna-CSRF": csrf };
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
        data: { name: "Путь архипелага", character_ids: [cid] },
      })
    ).json()
  ).id;
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("button", { name: "Обновить истории", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(sid);
  await expect(
    page.getByRole("heading", { name: "Путь архипелага", exact: true }),
  ).toBeVisible();
  const panel = page.getByLabel("Мир истории", { exact: true });
  await panel.getByText("Мир, время и события", { exact: true }).click();
  await panel
    .getByRole("combobox", { name: "Шаблон мира", exact: true })
    .selectOption(wid);
  await panel
    .getByRole("combobox", { name: "Шаблон сценария", exact: true })
    .selectOption(scid);
  await panel
    .getByRole("textbox", { name: "Seed случайного начала", exact: true })
    .fill("42");
  await panel
    .getByRole("button", { name: "Подтвердить подключение мира", exact: true })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    "снимок мира подключён",
  );
  await expect(panel.getByText(/Игровое время:/)).toContainText("00:00");
  await panel
    .getByRole("button", { name: "Подтвердить пропуск времени", exact: true })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    "Изменения мира применены",
  );
  await expect(panel.getByText(/Игровое время:/)).toContainText("01:00");
  await panel
    .getByText("Ресурсы, флаги и сюжетные линии", { exact: true })
    .click();
  await expect(
    panel.locator("pre").filter({ hasText: '"зерно": 8' }),
  ).toBeVisible();
  const packagePanel = page.getByLabel("Пакет истории", { exact: true });
  await packagePanel
    .getByText("Перенос всей истории с ветвями и состояниями", { exact: true })
    .click();
  const [exported] = await Promise.all([
    page.waitForEvent("download"),
    packagePanel
      .getByRole("button", { name: "Экспорт всей истории ZIP", exact: true })
      .click(),
  ]);
  const archive = readFileSync((await exported.path())!);
  await packagePanel
    .getByLabel("ZIP-пакет истории", { exact: true })
    .setInputFiles({
      name: "story.zip",
      mimeType: "application/zip",
      buffer: archive,
    });
  await packagePanel
    .getByRole("button", { name: "Предпросмотр пакета истории", exact: true })
    .click();
  await expect(
    packagePanel.getByRole("heading", { name: "Путь архипелага", exact: true }),
  ).toBeVisible();
  await packagePanel
    .getByRole("button", {
      name: "Подтвердить импорт всей истории",
      exact: true,
    })
    .click();
  await expect(packagePanel.getByRole("status")).toContainText(
    "История уже присутствует без отличий.",
  );
  await page
    .getByRole("button", { name: "Миры и сценарии", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Тип конструктора", exact: true })
    .selectOption("worlds");
  await world.getByText("JSON полного шаблона", { exact: true }).click();
  await world
    .getByRole("textbox", { name: "JSON шаблона", exact: true })
    .fill('{"entities":"неправильный тип"}');
  await world
    .getByRole("button", { name: "Проверить и применить JSON", exact: true })
    .click();
  await expect(world.getByRole("status")).toContainText(
    "не соответствуют схеме",
  );
  await expect(
    world.getByRole("textbox", { name: "Название шаблона", exact: true }),
  ).toHaveValue("Мир архипелага");
  await world
    .getByRole("textbox", { name: "JSON шаблона", exact: true })
    .fill('{"незавершённый":');
  await expect(world.getByRole("status")).toContainText(
    "Черновик шаблона автосохранён",
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Миры и сценарии", exact: true })
    .click();
  await world.getByText("Черновики шаблонов", { exact: true }).click();
  await world
    .getByRole("button", { name: "Восстановить черновик", exact: true })
    .first()
    .click();
  await world.getByText("JSON полного шаблона", { exact: true }).click();
  await expect(
    world.getByRole("textbox", { name: "JSON шаблона", exact: true }),
  ).toHaveValue('{"незавершённый":');
});
