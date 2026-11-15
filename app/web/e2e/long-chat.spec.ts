import { test, expect } from "./fixtures";

test("10 000 импортированных сообщений: страницы и ограниченный DOM", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  const csrf = (
    await (await page.request.get(appUrl + "/api/v1/session")).json()
  ).csrf;
  const template = (
    await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
  ).character;
  template.fields.name = "Мира";
  const card = await (
    await page.request.post(appUrl + "/api/v1/characters", {
      headers: { "X-Taverna-CSRF": csrf },
      data: { expected_revision: 0, character: template },
    })
  ).json();
  const rows = [
    { user_name: "Игрок", character_name: "Мира" },
    ...Array.from({ length: 10000 }, (_, i) => ({
      name: i % 2 ? "Мира" : "Игрок",
      is_user: i % 2 === 0,
      mes: `Сообщение ${i}`,
      send_date: "2025-01-01T00:00:00+00:00",
    })),
  ];
  const imported = await page.request.post(appUrl + "/api/v1/chat-import", {
    headers: { "X-Taverna-CSRF": csrf },
    data: {
      format: "jsonl",
      text: rows.map((row) => JSON.stringify(row)).join("\n"),
      name: "Длинная история",
      character_ids: [card.id],
      confirmed: true,
    },
    timeout: 60000,
  });
  expect(imported.ok(), await imported.text()).toBe(true);
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("button", { name: "Обновить истории", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption({ label: "Длинная история" });
  await expect(page.locator(".chat-message")).toHaveCount(100);
  await expect(
    page.locator(".chat-messages").getByText("Сообщение 9999", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Предыдущие сообщения", exact: true })
    .click();
  await expect(
    page.locator(".chat-messages").getByText("Сообщение 9899", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".chat-message")).toHaveCount(100);
  await page
    .getByRole("textbox", { name: "Поиск по истории", exact: true })
    .fill("Сообщение 1234");
  await page
    .getByRole("button", { name: "Найти в истории", exact: true })
    .click();
  await expect(page.locator(".chat-message")).toHaveCount(1);
  await expect(
    page.locator(".chat-messages").getByText("Сообщение 1234", { exact: true }),
  ).toBeVisible();
});
