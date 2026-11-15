import { test, expect } from "./fixtures";

test("Боковое меню прокручивается в невысоком окне; завершение доступно кнопкой", async ({
  page,
  appUrl,
}) => {
  await page.setViewportSize({ width: 1100, height: 480 });
  const nav = page.locator(".navigation");
  expect(await nav.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(
    true,
  );
  page.on("dialog", (dialog) => dialog.accept());
  await nav
    .getByRole("button", { name: "Завершить приложение", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Таверна завершает" }),
  ).toBeVisible();
  await expect
    .poll(async () => {
      try {
        return (
          await page.request.get(appUrl + "/api/v1/health", { timeout: 1000 })
        ).status();
      } catch {
        return 0;
      }
    })
    .toBe(0);
});
