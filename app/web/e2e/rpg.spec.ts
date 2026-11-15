import { test, expect } from "./fixtures";

test("RPG: формы каталогов, обмен, кубики, событие и отключение без потери данных", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
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
        data: { name: "Игровая история", character_ids: [cid] },
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
  const panel = page.getByLabel("Игровые модули RPG", { exact: true });
  await panel.getByText("Игровые модули RPG", { exact: true }).click();
  await panel.getByText("Настройки и каталоги RPG", { exact: true }).click();
  const labels = [
    "Инвентарь",
    "Характеристики",
    "Навыки",
    "Кубики",
    "Экономика",
    "Конфликты",
    "Случайные события",
  ];
  for (const label of labels)
    await panel
      .getByRole("checkbox", { name: label + ": включить модуль", exact: true })
      .check();
  await panel.getByText("Предметы и валюты", { exact: true }).click();
  await panel
    .getByRole("textbox", { name: "Ключ каталога RPG", exact: true })
    .fill("sword");
  await panel
    .getByRole("textbox", { name: "Название записи каталога", exact: true })
    .fill("Меч");
  await panel
    .getByRole("spinbutton", { name: "Вес предмета в граммах", exact: true })
    .fill("1500");
  await panel
    .getByRole("button", { name: "Добавить или обновить предмет", exact: true })
    .click();
  await panel
    .getByRole("textbox", { name: "Ключ каталога RPG", exact: true })
    .fill("gold");
  await panel
    .getByRole("textbox", { name: "Название записи каталога", exact: true })
    .fill("Золото");
  await panel
    .getByRole("button", { name: "Добавить или обновить валюту", exact: true })
    .click();
  await panel.getByText("Таблицы случайных событий", { exact: true }).click();
  await panel
    .getByRole("textbox", { name: "Ключ таблицы", exact: true })
    .fill("find");
  await panel
    .getByRole("textbox", { name: "Название таблицы", exact: true })
    .fill("Находки");
  await panel
    .getByRole("textbox", { name: "Название исхода", exact: true })
    .fill("Кошелёк");
  await panel
    .getByRole("textbox", { name: "Текст события", exact: true })
    .fill("Найден кошелёк с пятью монетами");
  await panel
    .getByRole("checkbox", { name: "Добавить последствие исхода", exact: true })
    .check();
  await panel
    .getByRole("textbox", { name: "Ключ последствия", exact: true })
    .fill("gold");
  await panel
    .getByRole("spinbutton", { name: "Изменение последствия", exact: true })
    .fill("5");
  await panel
    .getByRole("button", { name: "Добавить исход в таблицу", exact: true })
    .click();
  await panel
    .getByRole("textbox", {
      name: "Новый seed RNG (пусто: оставить прежний)",
      exact: true,
    })
    .fill("browser-rpg-seed");
  await panel
    .getByRole("button", { name: "Сохранить настройки RPG", exact: true })
    .click();
  await expect(panel.getByRole("status").first()).toContainText(
    "Настройки RPG сохранены",
  );
  await panel.getByText("Игровые операции", { exact: true }).click();
  const actor = panel.getByRole("combobox", {
      name: "Участник RPG",
      exact: true,
    }),
    operation = panel.getByRole("combobox", {
      name: "Игровая операция",
      exact: true,
    }),
    submit = panel.getByRole("button", {
      name: "Подтвердить игровую операцию",
      exact: true,
    });
  const player = await actor.inputValue();
  async function perform(kind: string, key?: string, amount?: number) {
    await operation.selectOption(kind);
    if (key !== undefined)
      await panel
        .getByRole("textbox", { name: "Ключ игрового параметра", exact: true })
        .fill(key);
    if (amount !== undefined)
      await panel
        .getByRole("spinbutton", {
          name: "Значение или поправка RPG",
          exact: true,
        })
        .fill(String(amount));
    await submit.click();
    await expect(panel.getByRole("status").first()).toContainText(
      "Игровая операция сохранена",
    );
  }
  await perform("adjust_currency", "gold", 10);
  await actor.selectOption(cid);
  await perform("adjust_item", "sword", 2);
  await actor.selectOption(player);
  await operation.selectOption("trade");
  await panel
    .getByRole("combobox", { name: "Другой участник RPG", exact: true })
    .selectOption(cid);
  await panel
    .getByRole("textbox", { name: "Ключ игрового параметра", exact: true })
    .fill("sword");
  await panel
    .getByRole("combobox", { name: "Валюта покупки", exact: true })
    .selectOption("gold");
  await panel
    .getByRole("spinbutton", { name: "Цена за предмет", exact: true })
    .fill("4");
  await submit.click();
  await expect(
    panel.getByRole("listitem").filter({ hasText: "Меч: 1" }),
  ).toBeVisible();
  await expect(
    panel.getByRole("listitem").filter({ hasText: "Золото: 6" }),
  ).toBeVisible();
  await perform("roll");
  await expect(panel.getByRole("status").last()).toContainText("Броски:");
  await perform("random_event", "find");
  await expect(panel.getByRole("status").last()).toContainText(
    "Найден кошелёк",
  );
  await expect(
    panel.getByRole("listitem").filter({ hasText: "Золото: 11" }),
  ).toBeVisible();
  for (const label of labels)
    await panel
      .getByRole("checkbox", { name: label + ": включить модуль", exact: true })
      .uncheck();
  await panel
    .getByRole("button", { name: "Сохранить настройки RPG", exact: true })
    .click();
  await expect(panel.getByRole("status").first()).toContainText(
    "Настройки RPG сохранены",
  );
  await expect(submit).toBeDisabled();
  await expect(
    panel.getByRole("listitem").filter({ hasText: "Меч: 1" }),
  ).toBeVisible();
  const bundle = await (
    await page.request.get(appUrl + `/api/v1/stories/${sid}`)
  ).json();
  expect(bundle.state.payload.rpg.actors[player].balances.gold).toBe(11);
  expect(bundle.state.payload.rpg.actors[cid].inventory.sword).toBe(1);
  await panel.getByText("JSON и черновики RPG", { exact: true }).click();
  await panel
    .getByRole("textbox", { name: "JSON настроек RPG", exact: true })
    .fill("{незавершённый RPG");
  await panel
    .getByRole("button", { name: "Проверить JSON RPG", exact: true })
    .click();
  await expect(panel.getByRole("status").first()).toContainText(
    "Черновик RPG автосохранён",
  );
  page.on("dialog", (d) => d.accept());
  await page.reload();
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(sid);
  await panel.getByText("Игровые модули RPG", { exact: true }).click();
  await panel.getByText("JSON и черновики RPG", { exact: true }).click();
  await panel
    .getByRole("button", { name: /Восстановить черновик RPG / })
    .first()
    .click();
  await expect(
    panel.getByRole("textbox", { name: "JSON настроек RPG", exact: true }),
  ).toHaveValue("{незавершённый RPG");
});
