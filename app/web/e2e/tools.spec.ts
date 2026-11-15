import { test, expect } from "./fixtures";

test("Материалы и инструменты: кириллица, поиск, заметки, имена, шаблон с правами и без повторного эффекта", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(90000);
  await page
    .getByRole("button", { name: "Материалы и инструменты", exact: true })
    .click();
  const library = page.getByLabel("Библиотека материалов", { exact: true });
  await library.getByLabel("Файл документа", { exact: true }).setInputFiles({
    name: "Справочник гавани.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Кириллица: гавань и маяк.", "utf8"),
  });
  await library
    .getByRole("button", { name: "Подтвердить импорт документа", exact: true })
    .click();
  await expect(library.getByRole("status")).toContainText(
    "Документ импортирован",
  );
  await library
    .getByLabel("Описание материала", { exact: true })
    .fill("Заметки о городе");
  await library
    .getByRole("button", {
      name: "Сохранить метаданные материала",
      exact: true,
    })
    .click();
  await expect(library.getByRole("status")).toContainText(
    "Метаданные материала сохранены",
  );
  const download = page.waitForEvent("download");
  await library
    .getByRole("button", { name: "Скачать исходный документ", exact: true })
    .click();
  expect((await download).suggestedFilename()).toBe("Справочник гавани.txt");
  await library
    .getByLabel("Поиск в материалах", { exact: true })
    .fill("КИРИЛЛИЦА");
  await library
    .getByRole("button", { name: "Найти материалы", exact: true })
    .click();
  await expect(
    library.getByRole("button", {
      name: "Справочник гавани.txt · Справочник гавани.txt",
      exact: true,
    }),
  ).toBeVisible();
  const tools = page.getByLabel("Заметки и инструменты", { exact: true });
  await tools
    .getByRole("button", { name: "Создать: Словарь мира", exact: true })
    .click();
  await tools.getByLabel("Название записи", { exact: true }).fill("Гавань");
  await tools
    .getByLabel("Определение и пояснения", { exact: true })
    .fill("Тихая стоянка кораблей.");
  await tools
    .getByRole("button", { name: "Сохранить запись инструмента", exact: true })
    .click();
  await expect(tools.getByRole("status")).toContainText("опубликована");
  await tools
    .getByRole("button", { name: "Создать: Генератор имён", exact: true })
    .click();
  await tools
    .getByRole("button", { name: "Сохранить запись инструмента", exact: true })
    .click();
  await tools
    .getByRole("button", { name: "Сгенерировать имена", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (
          await tools
            .getByLabel("Результат генератора имён", { exact: true })
            .textContent()
        )?.split("\n").length,
    )
    .toBe(10);
  const first = await tools
    .getByLabel("Результат генератора имён", { exact: true })
    .textContent();
  expect(first?.split("\n")).toHaveLength(10);
  await tools
    .getByRole("button", { name: "Сгенерировать имена", exact: true })
    .click();
  await expect(
    tools.getByLabel("Результат генератора имён", { exact: true }),
  ).toHaveText(first!);
  await tools
    .getByRole("button", {
      name: "Создать: Пользовательский шаблон",
      exact: true,
    })
    .click();
  await tools
    .getByLabel("Название записи", { exact: true })
    .fill("Приветствие моряку");
  await tools.getByLabel("Разрешить создание заметки", { exact: true }).check();
  await tools
    .getByRole("button", { name: "Сохранить запись инструмента", exact: true })
    .click();
  await tools.getByLabel("Переменная: имя", { exact: true }).fill("Мира");
  await tools
    .getByRole("button", { name: "Предпросмотр действия шаблона", exact: true })
    .click();
  await expect(tools.getByRole("status")).toContainText(
    "Действие ещё не выполнено",
  );
  const preview = tools.locator("details").filter({
    has: page.locator("summary").filter({ hasText: "Приветствие моряку ·" }),
  });
  await preview.locator("summary").first().click();
  await expect(preview.locator("pre")).toHaveText("Привет, Мира!");
  await preview
    .getByRole("button", {
      name: "Подтвердить действие с указанным правом",
      exact: true,
    })
    .click();
  await expect(tools.getByRole("status")).toContainText("выполнено один раз");
  const notes = await (
    await page.request.get(appUrl + "/api/v1/tools?kind=note")
  ).json();
  expect(notes.items).toHaveLength(1);
  expect(notes.items[0].payload.text).toBe("Привет, Мира!");
  await tools
    .getByRole("button", { name: "Создать: Закладка", exact: true })
    .click();
  await tools
    .getByRole("button", { name: "Добавить URL", exact: true })
    .click();
  await tools
    .getByLabel("URL закладки", { exact: true })
    .fill("javascript:alert(1)");
  await tools
    .getByRole("button", { name: "Сохранить запись инструмента", exact: true })
    .click();
  await expect(tools.getByRole("status")).toContainText("Проверьте");
  await tools.getByText("JSON записи инструмента", { exact: true }).click();
  await tools
    .getByLabel("JSON записи инструмента", { exact: true })
    .fill('{"incomplete":');
  await expect(
    tools.getByText("Черновик инструмента автосохранён.", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "Материалы и инструменты", exact: true })
    .click();
  await tools.getByText("Черновики инструментов", { exact: true }).click();
  await tools
    .getByRole("button", { name: /Восстановить инструмент/ })
    .first()
    .click();
  await tools.getByText("JSON записи инструмента", { exact: true }).click();
  await expect(
    tools.getByLabel("JSON записи инструмента", { exact: true }),
  ).toHaveValue('{"incomplete":');
});
