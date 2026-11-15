import { test, expect } from "./fixtures";

test("Чат без модели, режимы, редактирование и возврат к оригиналу", async ({
  page,
  appUrl,
}) => {
  const session = await page.request.get(appUrl + "/api/v1/session");
  const csrf = (await session.json()).csrf;
  const template = (
    await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
  ).character;
  template.fields.name = "Мира";
  template.fields.first_mes = "Приветствие из карточки";
  const saved = await page.request.post(appUrl + "/api/v1/characters", {
    headers: { "X-Taverna-CSRF": csrf },
    data: { expected_revision: 0, character: template },
  });
  expect(saved.ok()).toBe(true);
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("button", { name: "Обновить истории", exact: true })
    .click();
  await page.getByText("Новая независимая история", { exact: true }).click();
  await page
    .getByRole("textbox", { name: "Название истории", exact: true })
    .fill("Проверочная история");
  await page.getByRole("checkbox", { name: "Мира", exact: true }).check();
  await page
    .getByRole("button", { name: "Создать историю", exact: true })
    .click();
  await expect(
    page
      .locator(".chat-message")
      .getByText("Приветствие из карточки", { exact: true }),
  ).toBeVisible();
  const originalBranch = await page
    .getByRole("combobox", { name: "Активная ветвь", exact: true })
    .inputValue();
  await page
    .getByRole("textbox", { name: "Ваше сообщение", exact: true })
    .fill("Моя реплика без обязательной персоны");
  await page
    .getByRole("button", { name: "Отправить сообщение", exact: true })
    .click();
  await expect(
    page
      .locator(".chat-messages")
      .getByText("Моя реплика без обязательной персоны", { exact: true }),
  ).toBeVisible();
  for (const mode of ["literary", "novel", "messenger"]) {
    await page
      .getByRole("combobox", { name: "Вид чата", exact: true })
      .selectOption(mode);
    await expect(
      page.getByRole("combobox", { name: "Вид чата", exact: true }),
    ).toHaveValue(mode);
  }
  await page
    .getByRole("button", { name: "Редактировать", exact: true })
    .last()
    .click();
  await page
    .getByRole("textbox", { name: "Новый текст сообщения", exact: true })
    .fill("Новый вариант реплики");
  await page
    .getByRole("button", {
      name: "Сохранить правку в новой ветви",
      exact: true,
    })
    .click();
  await expect(
    page
      .locator(".chat-messages")
      .getByText("Новый вариант реплики", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Активная ветвь", exact: true }),
  ).not.toHaveValue(originalBranch);
  await page
    .getByRole("combobox", { name: "Активная ветвь", exact: true })
    .selectOption(originalBranch);
  await expect(
    page
      .locator(".chat-messages")
      .getByText("Моя реплика без обязательной персоны", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".chat-messages")
      .getByText("Новый вариант реплики", { exact: true }),
  ).not.toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  const stories = page.getByRole("combobox", {
    name: "Сохранённая история",
    exact: true,
  });
  await stories.selectOption({ label: "Проверочная история" });
  await expect(
    page
      .locator(".chat-messages")
      .getByText("Моя реплика без обязательной персоны", { exact: true }),
  ).toBeVisible();
  expect(await page.locator(".chat-message").count()).toBeLessThanOrEqual(100);
});
