import { test, expect } from "./fixtures";

test("резервная копия: создание, контроль и скачивание через интерфейс", async ({
  page,
  appUrl,
}) => {
  expect(appUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  await page.getByRole("button", { name: "Резервные копии", exact: true }).click();
  const region = page.getByRole("region", { name: "Резервные копии" });
  await expect(
    region.getByRole("heading", { name: "Локальные резервные копии" }),
  ).toBeVisible();

  await region
    .getByRole("button", { name: "Создать резервную копию" })
    .click();
  await expect(region.getByRole("status")).toContainText(
    "создана и проверена",
    { timeout: 20000 },
  );
  const manual = region.locator("li").filter({ hasText: "taverna-manual-" });
  await expect(manual).toHaveCount(1);
  const filename = (await manual.locator("strong").textContent()) || "";
  expect(filename).toMatch(/^taverna-manual-\d{8}T\d{6}Z-[0-9a-f]{32}\.zip$/);

  await manual.getByRole("button", { name: "Проверить" }).click();
  await expect(region.getByRole("status")).toContainText("Архив проверен", {
    timeout: 15000,
  });

  const received = page.waitForEvent("download");
  await manual.getByRole("button", { name: "Скачать" }).click();
  const download = await received;
  expect(download.suggestedFilename()).toBe(filename);
});
