import { test, expect } from "./fixtures";

test("Профиль, наследование и предпросмотр действительного контекста", async ({
  page,
  appUrl,
}) => {
  const csrf = (
    await (await page.request.get(appUrl + "/api/v1/session")).json()
  ).csrf;
  const template = (
    await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
  ).character;
  template.fields.name = "Мира";
  template.fields.description = "Смотрительница таверны";
  template.fields.creator_notes = "Скрытые заметки автора";
  const actor = await (
    await page.request.post(appUrl + "/api/v1/characters", {
      headers: { "X-Taverna-CSRF": csrf },
      data: { expected_revision: 0, character: template },
    })
  ).json();
  const story = await (
    await page.request.post(appUrl + "/api/v1/stories", {
      headers: { "X-Taverna-CSRF": csrf },
      data: { name: "История с профилем", character_ids: [actor.id] },
    })
  ).json();
  await page
    .getByRole("button", { name: "Профили и промпты", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Название профиля", exact: true })
    .fill("Браузерный профиль");
  await page
    .getByRole("spinbutton", { name: "Резерв ответа", exact: true })
    .fill("512");
  await page
    .getByRole("spinbutton", { name: "Температура", exact: true })
    .fill("0.4");
  await page
    .getByRole("button", { name: "Сохранить профиль", exact: true })
    .click();
  await expect(
    page.getByText("Профиль сохранён.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Назначаемый профиль", exact: true })
    .selectOption({ label: "Браузерный профиль" });
  await page
    .getByRole("button", { name: "Сохранить назначение", exact: true })
    .click();
  await expect(
    page.getByText("Назначение сохранено.", { exact: true }),
  ).toBeVisible();
  const resolved = await (
    await page.request.post(appUrl + "/api/v1/profiles/resolve", {
      headers: { "X-Taverna-CSRF": csrf },
      data: {},
    })
  ).json();
  expect(resolved.settings.parameters.max_tokens).toBe(512);
  expect(resolved.settings.parameters.temperature).toBe(0.4);
  await page.getByRole("button", { name: "Истории", exact: true }).click();
  await page
    .getByRole("button", { name: "Обновить истории", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Сохранённая история", exact: true })
    .selectOption(story.id);
  await page
    .getByText("Профиль и инспектор контекста", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "Предпросмотр контекста", exact: true })
    .click();
  const inspector = page.locator(".diagnostic:visible");
  await expect(inspector).toContainText("Смотрительница таверны");
  await expect(inspector).toContainText('"reply_reserve": 512');
  await expect(inspector).toContainText('"exact_tokens": false');
  await expect(inspector).not.toContainText("Скрытые заметки автора");
  await page.reload();
  await page
    .getByRole("button", { name: "Профили и промпты", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Сохранённый профиль", exact: true })
    .selectOption({ label: "Браузерный профиль" });
  await expect(
    page.getByRole("spinbutton", { name: "Резерв ответа", exact: true }),
  ).toHaveValue("512");
});
