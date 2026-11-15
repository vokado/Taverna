import { test, expect } from "./fixtures";

test("Режиссёр: ручное состояние NPC, политика, черновики и конфликт фоновой ревизии", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  const csrf = (
    await (await page.request.get(appUrl + "/api/v1/session")).json()
  ).csrf;
  const headers = { "X-Taverna-CSRF": csrf };
  const ids: string[] = [];
  for (const name of ["Мира", "Дан"]) {
    const card = (
      await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
    ).character;
    card.fields.name = name;
    ids.push(
      (
        await (
          await page.request.post(appUrl + "/api/v1/characters", {
            headers,
            data: { expected_revision: 0, character: card },
          })
        ).json()
      ).id,
    );
  }
  const story = await (
    await page.request.post(appUrl + "/api/v1/stories", {
      headers,
      data: { name: "Проверка режиссёра", character_ids: ids },
    })
  ).json();
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("button", { name: "Обновить истории", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(story.id);
  const panel = page.getByLabel("Режиссёр и состояния NPC", { exact: true });
  await panel.getByText("Режиссёр, характер и эмоции", { exact: true }).click();
  await panel
    .getByRole("combobox", { name: "Режим характера в ветви", exact: true })
    .selectOption("developing");
  await panel
    .getByRole("combobox", { name: "Текущая эмоция", exact: true })
    .selectOption("радость");
  await panel
    .getByRole("button", { name: "Сохранить состояние NPC", exact: true })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    "Состояние NPC сохранено",
  );
  await panel
    .getByText("Автономность и настройки режиссёра", { exact: true })
    .click();
  await panel
    .getByRole("checkbox", {
      name: "Включить режиссёра для ветви",
      exact: true,
    })
    .check();
  await panel
    .getByRole("checkbox", {
      name: "Готовить предложения до ответа",
      exact: true,
    })
    .uncheck();
  await panel
    .getByRole("button", { name: "Сохранить настройки режиссёра", exact: true })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    "Настройки режиссёра сохранены",
  );
  await panel
    .getByRole("combobox", { name: "Редактируемый NPC", exact: true })
    .selectOption(ids[1]);
  await expect(
    panel.getByRole("combobox", { name: "Текущая эмоция", exact: true }),
  ).toHaveValue("спокойствие");
  await panel
    .getByRole("combobox", { name: "Редактируемый NPC", exact: true })
    .selectOption(ids[0]);
  await expect(
    panel.getByRole("combobox", { name: "Текущая эмоция", exact: true }),
  ).toHaveValue("радость");
  await panel
    .getByText("JSON состояний и черновики режиссёра", { exact: true })
    .click();
  const raw = panel.getByRole("textbox", {
    name: "JSON редактора режиссёра",
    exact: true,
  });
  await raw.fill("{не закончено");
  await panel
    .getByRole("button", { name: "Проверить JSON состояний", exact: true })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    /JSON|Unexpected|Expected/,
  );
  await expect(panel.getByRole("status")).toContainText(
    "Черновик режиссёра автосохранён",
  );
  // An external backend write must refresh the owner view without losing input or
  // silently rebasing the editor. No fake LM Studio is involved in this UI case.
  const bundle = await (
    await page.request.get(appUrl + `/api/v1/stories/${story.id}`)
  ).json();
  const base = appUrl + `/api/v1/stories/${story.id}/director`;
  const input = page.getByRole("textbox", {
    name: "Ваше сообщение",
    exact: true,
  });
  await input.fill("Неотправленный текст");
  const updated = await page.request.put(base + `/npcs/${ids[0]}`, {
    headers,
    data: {
      branch_id: bundle.branch.id,
      expected_revision: bundle.branch.revision,
      patch: { emotion: { label: "интерес" } },
      reason: "Внешняя проверяемая правка",
    },
  });
  expect(updated.ok(), await updated.text()).toBe(true);
  await expect(
    panel.getByText(/Ветвь изменилась после начала редактирования/),
  ).toBeVisible({ timeout: 15000 });
  await expect(input).toHaveValue("Неотправленный текст");
  await expect(raw).toHaveValue("{не закончено");
  await panel
    .getByRole("combobox", { name: "Текущая эмоция", exact: true })
    .selectOption("печаль");
  await panel
    .getByRole("button", { name: "Сохранить состояние NPC", exact: true })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    /измен|конфликт|верси|ревизи/i,
  );
  const state = await (
    await page.request.get(base + `?branch_id=${bundle.branch.id}`)
  ).json();
  expect(state.characters[ids[0]].emotion.label).toBe("интерес");
  await input.fill("");
  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(story.id);
  await panel.getByText("Режиссёр, характер и эмоции", { exact: true }).click();
  await panel
    .getByText("JSON состояний и черновики режиссёра", { exact: true })
    .click();
  await panel
    .getByRole("button", {
      name: "Восстановить черновик режиссёра",
      exact: true,
    })
    .first()
    .click();
  await expect(raw).toHaveValue("{не закончено");
  await expect(
    panel.getByRole("combobox", { name: "Текущая эмоция", exact: true }),
  ).toHaveValue("печаль");
});
