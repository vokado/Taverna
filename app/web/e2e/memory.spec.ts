import { readFileSync } from "node:fs";
import { test, expect } from "./fixtures";

test("Память: тайна NPC, автосохранение, отношения с игроком, поиск и импорт", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  const csrf = (
    await (await page.request.get(appUrl + "/api/v1/session")).json()
  ).csrf;
  const headers = { "X-Taverna-CSRF": csrf };
  const ids: string[] = [];
  for (const name of ["Мира", "Орн"]) {
    const card = (
      await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
    ).character;
    card.fields.name = name;
    const response = await page.request.post(appUrl + "/api/v1/characters", {
      headers,
      data: { expected_revision: 0, character: card },
    });
    ids.push((await response.json()).id);
  }
  const story = (
    await (
      await page.request.post(appUrl + "/api/v1/stories", {
        headers,
        data: { name: "Память таверны", character_ids: ids },
      })
    ).json()
  ).id;
  const other = (
    await (
      await page.request.post(appUrl + "/api/v1/stories", {
        headers,
        data: { name: "Импорт памяти", character_ids: ids },
      })
    ).json()
  ).id;
  const firstBundle = await (
    await page.request.get(appUrl + `/api/v1/stories/${story}`)
  ).json();
  await page.request.post(appUrl + `/api/v1/stories/${story}/messages`, {
    headers,
    data: {
      branch_id: firstBundle.branch.id,
      expected_revision: firstBundle.branch.revision,
      text: "Мира нашла шкатулку",
    },
  });
  async function open(id: string) {
    await page.getByRole("button", { name: "Истории", exact: true }).click();
    await page
      .getByRole("button", { name: "Обновить истории", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Сохранённая история", exact: true })
      .selectOption(id);
    await expect(
      page.getByRole("heading", {
        name: id === story ? "Память таверны" : "Импорт памяти",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByText("Память, знания и отношения", { exact: true }).click();
  }
  await open(story);
  const panel = page.getByLabel("Память истории", { exact: true });
  await panel
    .getByRole("textbox", { name: "Текст памяти", exact: true })
    .fill("Пароль шкатулки — Звезда");
  await panel
    .getByRole("combobox", { name: "Тип знания", exact: true })
    .selectOption("secret");
  await panel
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await expect(
    panel.getByText("Запись памяти сохранена.", { exact: true }),
  ).toBeVisible();
  await panel
    .getByRole("combobox", { name: "Просмотр памяти", exact: true })
    .selectOption(ids[1]);
  await expect(
    panel
      .getByRole("combobox", { name: "Сохранённая запись памяти", exact: true })
      .locator("option"),
  ).toHaveCount(1);
  await panel
    .getByRole("combobox", { name: "Просмотр памяти", exact: true })
    .selectOption("");
  await panel
    .getByRole("button", { name: "Новая запись памяти", exact: true })
    .click();
  await panel
    .getByRole("textbox", { name: "Текст памяти", exact: true })
    .fill("Сохранённый черновик воспоминания");
  await expect(
    panel.getByText("Черновик памяти автосохранён.", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await open(story);
  await panel
    .getByRole("button", { name: "Восстановить черновик памяти", exact: true })
    .click();
  await expect(
    panel.getByRole("textbox", { name: "Текст памяти", exact: true }),
  ).toHaveValue("Сохранённый черновик воспоминания");
  await panel
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await expect(
    panel.getByText("Запись памяти сохранена.", { exact: true }),
  ).toBeVisible();
  let bundle = await (
    await page.request.get(appUrl + `/api/v1/stories/${story}`)
  ).json();
  expect(
    (
      await (
        await page.request.get(
          appUrl +
            `/api/v1/stories/${story}/memories/draft?branch_id=${bundle.branch.id}`,
        )
      ).json()
    ).draft,
  ).toBeNull();
  await panel
    .getByRole("button", { name: "Новая запись памяти", exact: true })
    .click();
  await panel
    .getByRole("combobox", { name: "Уровень памяти", exact: true })
    .selectOption("relationship");
  await panel
    .getByRole("textbox", { name: "Текст памяти", exact: true })
    .fill("Мира доверяет игроку");
  page.once("dialog", (d) => d.accept("Доверие"));
  await panel
    .getByRole("button", { name: "Добавить показатель отношений", exact: true })
    .click();
  await panel
    .getByRole("spinbutton", { name: "Доверие", exact: true })
    .fill("25");
  await panel
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await expect(
    panel.getByText("Запись памяти сохранена.", { exact: true }),
  ).toBeVisible();
  bundle = await (
    await page.request.get(appUrl + `/api/v1/stories/${story}`)
  ).json();
  const relations = Object.values(bundle.state.payload.relationships) as Array<{
    relation: { parties: string[]; metrics: Record<string, number> };
  }>;
  expect(relations).toHaveLength(1);
  expect(relations[0].relation.metrics.Доверие).toBe(25);
  expect(relations[0].relation.parties).toContain(ids[0]);
  await panel
    .getByRole("textbox", { name: "Поиск памяти", exact: true })
    .fill("черновик");
  await panel
    .getByRole("button", { name: "Найти в памяти", exact: true })
    .click();
  const result = panel.getByRole("region", {
    name: "Результат операции памяти",
    exact: true,
  });
  await expect(
    result.getByText("Сохранённый черновик воспоминания", { exact: true }),
  ).toBeVisible();
  await expect(
    result.getByText("Текстовый поиск", { exact: true }),
  ).toBeVisible();
  await panel.getByText("Экспорт и импорт памяти", { exact: true }).click();
  const downloadEvent = page.waitForEvent("download");
  await panel
    .getByRole("button", { name: "Экспорт памяти JSON", exact: true })
    .click();
  const downloaded = await downloadEvent;
  const path = await downloaded.path();
  expect(path).toBeTruthy();
  const bytes = readFileSync(path!);
  const pkg = JSON.parse(bytes.toString("utf8"));
  expect(pkg.data.records).toHaveLength(3);
  await open(other);
  await panel.getByText("Экспорт и импорт памяти", { exact: true }).click();
  await panel.getByLabel("Пакет памяти", { exact: true }).setInputFiles({
    name: "memory.json",
    mimeType: "application/json",
    buffer: bytes,
  });
  await panel
    .getByRole("checkbox", {
      name: "Сохранить отсутствующие сообщения как внешнее происхождение",
      exact: true,
    })
    .check();
  await panel
    .getByRole("button", { name: "Предпросмотр импорта памяти", exact: true })
    .click();
  await expect(
    panel.getByRole("button", {
      name: "Подтвердить импорт памяти",
      exact: true,
    }),
  ).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await panel
    .getByRole("button", { name: "Подтвердить импорт памяти", exact: true })
    .click();
  await expect(
    panel.getByText("Память импортирована.", { exact: true }),
  ).toBeVisible();
  const target = await (
    await page.request.get(appUrl + `/api/v1/stories/${other}`)
  ).json();
  expect(Object.keys(target.state.payload.memory)).toHaveLength(2);
  expect(Object.keys(target.state.payload.relationships)).toHaveLength(1);
  expect(target.branch.id).not.toBe(bundle.branch.id);
  const privacy = await (
    await page.request.post(
      appUrl + `/api/v1/stories/${other}/context-preview`,
      {
        headers,
        data: {
          branch_id: target.branch.id,
          expected_revision: target.branch.revision,
          character_id: ids[1],
        },
      },
    )
  ).json();
  expect(JSON.stringify(privacy.messages)).not.toContain("Пароль шкатулки");
});

test("Отдельные черновики переживают смену записи и истории, ошибочный JSON сохраняется", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const csrf = (
      await (await page.request.get(appUrl + "/api/v1/session")).json()
    ).csrf,
    headers = { "X-Taverna-CSRF": csrf };
  const template = (
    await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
  ).character;
  template.fields.name = "Мира";
  const actor = (
    await (
      await page.request.post(appUrl + "/api/v1/characters", {
        headers,
        data: { expected_revision: 0, character: template },
      })
    ).json()
  ).id;
  const stories: string[] = [];
  for (const name of ["Черновики памяти", "Другая ветвь данных"]) {
    stories.push(
      (
        await (
          await page.request.post(appUrl + "/api/v1/stories", {
            headers,
            data: { name, character_ids: [actor] },
          })
        ).json()
      ).id,
    );
  }
  async function open(index: number) {
    await page.getByRole("button", { name: "Истории", exact: true }).click();
    await page
      .getByRole("button", { name: "Обновить истории", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Сохранённая история", exact: true })
      .selectOption(stories[index]);
    await expect(
      page.getByRole("heading", {
        name: index ? "Другая ветвь данных" : "Черновики памяти",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByText("Память, знания и отношения", { exact: true }).click();
  }
  await open(0);
  const panel = page.getByLabel("Память истории", { exact: true });
  await panel
    .getByRole("textbox", { name: "Текст памяти", exact: true })
    .fill("Первый незавершённый факт");
  await panel
    .getByRole("button", { name: "Новая запись памяти", exact: true })
    .click();
  await expect(
    panel.getByRole("textbox", { name: "Текст памяти", exact: true }),
  ).toHaveValue("");
  await panel
    .getByRole("textbox", { name: "Текст памяти", exact: true })
    .fill("Второй незавершённый факт");
  await panel
    .getByRole("button", { name: "Новая запись памяти", exact: true })
    .click();
  const bundle = await (
      await page.request.get(appUrl + `/api/v1/stories/${stories[0]}`)
    ).json(),
    bid = bundle.branch.id;
  const path =
    appUrl + `/api/v1/stories/${stories[0]}/memories/draft?branch_id=${bid}`;
  let saved = await (await page.request.get(path)).json();
  expect(saved.drafts).toHaveLength(2);
  const first = saved.drafts.find(
    (d: { payload: { form: { text: string } } }) =>
      d.payload.form.text === "Первый незавершённый факт",
  );
  expect(first).toBeTruthy();
  await panel
    .getByRole("combobox", { name: "Сохранённый черновик памяти", exact: true })
    .selectOption(first.id);
  await panel
    .getByRole("button", { name: "Восстановить черновик памяти", exact: true })
    .click();
  await panel.getByText("Полные поля записи JSON", { exact: true }).click();
  const invalid = '{"knower_ids":"не массив","text":42}';
  await panel
    .getByRole("textbox", { name: "Данные записи памяти", exact: true })
    .fill(invalid);
  await panel
    .getByRole("button", { name: "Применить JSON памяти к форме", exact: true })
    .click();
  await expect(
    panel.getByText("Проверьте формат и обязательные поля запроса.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel.getByRole("textbox", { name: "Текст памяти", exact: true }),
  ).toHaveValue("Первый незавершённый факт");
  await open(1);
  await open(0);
  saved = await (await page.request.get(path)).json();
  const restored = saved.drafts.find((d: { id: string }) => d.id === first.id);
  expect(restored.payload.raw).toBe(invalid);
  expect(restored.payload.form.text).toBe("Первый незавершённый факт");
  await panel
    .getByRole("combobox", { name: "Сохранённый черновик памяти", exact: true })
    .selectOption(first.id);
  await panel
    .getByRole("button", { name: "Восстановить черновик памяти", exact: true })
    .click();
  await expect(
    panel.getByRole("button", { name: "Сохранить память", exact: true }),
  ).toBeDisabled();
  await panel.getByText("Полные поля записи JSON", { exact: true }).click();
  await expect(
    panel.getByRole("textbox", { name: "Данные записи памяти", exact: true }),
  ).toHaveValue(invalid);
  await panel
    .getByRole("textbox", { name: "Данные записи памяти", exact: true })
    .fill(JSON.stringify(restored.payload.form));
  await panel
    .getByRole("button", { name: "Применить JSON памяти к форме", exact: true })
    .click();
  await panel
    .getByRole("button", { name: "Сохранить память", exact: true })
    .click();
  await expect(
    panel.getByText("Запись памяти сохранена.", { exact: true }),
  ).toBeVisible();
  saved = await (await page.request.get(path)).json();
  expect(saved.drafts).toHaveLength(1);
  expect(saved.drafts[0].payload.form.text).toBe("Второй незавершённый факт");
  expect(errors).toEqual([]);
});
