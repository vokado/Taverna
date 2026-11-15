import { test, expect } from "./fixtures";
import { createServer } from "node:http";
import { readFileSync, existsSync, copyFileSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

test("Редактор: главы, версии, сохранённый черновик, ИИ preview/reject и четыре экспорта", async ({
  page,
  appUrl,
}) => {
  test.setTimeout(120000);
  const calls: string[] = [];
  const server = createServer((req, res) => {
    calls.push(req.method + " " + req.url);
    if (req.url === "/api/v1/models") {
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify({
          models: [
            {
              type: "llm",
              key: "test-editor",
              display_name: "Тестовый HTTP редактор",
              loaded_instances: [
                { id: "editor-instance", config: { context_length: 8192 } },
              ],
            },
          ],
        }),
      );
      return;
    }
    if (req.url === "/v1/chat/completions" && req.method === "POST") {
      req.resume();
      req.on("end", () => {
        res.setHeader("Content-Type", "text/event-stream");
        res.end(
          "data: " +
            JSON.stringify({
              choices: [{ delta: { content: "Связная новая проза о море." } }],
            }) +
            "\n\ndata: " +
            JSON.stringify({
              choices: [{ delta: {}, finish_reason: "stop" }],
            }) +
            "\n\ndata: [DONE]\n\n",
        );
      });
      return;
    }
    res.statusCode = 404;
    res.end("{}");
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  try {
    const address = server.address();
    if (!address || typeof address === "string") throw Error("fixture port");
    const csrf = (
      await (await page.request.get(appUrl + "/api/v1/session")).json()
    ).csrf;
    await page.request.put(appUrl + "/api/v1/models/connection", {
      headers: { "X-Taverna-CSRF": csrf },
      data: {
        expected_revision: 0,
        endpoint: "http://127.0.0.1:" + address.port,
      },
    });
    await page
      .getByRole("button", { name: "Литературный редактор", exact: true })
      .click();
    const editor = page.getByLabel("Литературный редактор", { exact: true });
    await editor
      .getByLabel("Название книги", { exact: true })
      .fill("Море и 🌊");
    await editor
      .getByRole("button", { name: "Добавить главу", exact: true })
      .click();
    await editor.getByLabel("Название главы", { exact: true }).fill("Гавань");
    await editor
      .getByRole("textbox", { name: "Текст главы", exact: true })
      .fill("Моряк вошёл в порт.\n\nОн увидел море.");
    await editor
      .getByRole("button", { name: "Опубликовать рукопись", exact: true })
      .click();
    await expect(editor.getByRole("status")).toContainText("Версия 1");
    await editor
      .getByRole("textbox", { name: "Текст главы", exact: true })
      .fill("Старая проза о море.");
    await editor
      .getByRole("button", { name: "Опубликовать рукопись", exact: true })
      .click();
    await expect(editor.getByRole("status")).toContainText("Версия 2");
    await editor
      .getByText("ИИ-редактор и преобразование чата", { exact: true })
      .click();
    await editor
      .getByRole("button", {
        name: "Обновить уже загруженные модели",
        exact: true,
      })
      .click();
    await expect(editor.getByLabel("Модель редактора")).toContainText(
      "Тестовый HTTP редактор",
    );
    await editor.getByLabel("Модель редактора").selectOption("editor-instance");
    await editor
      .getByRole("button", {
        name: "Получить предпросмотр правки",
        exact: true,
      })
      .click();
    await expect(
      editor.getByText("Предложение готово", { exact: true }),
    ).toBeVisible();
    await expect(
      editor.getByRole("textbox", { name: "Текст главы", exact: true }),
    ).toHaveValue("Старая проза о море.");
    const proposals = await (
      await page.request.get(appUrl + "/api/v1/manuscripts/proposals")
    ).json();
    expect(proposals.items).toHaveLength(1);
    const pending = editor.locator("details").filter({
      has: page
        .locator("summary")
        .filter({ hasText: proposals.items[0].id.slice(0, 8) }),
    });
    await pending.locator("summary").first().click();
    await expect(
      pending.locator("pre").filter({ hasText: "Связная новая проза" }).first(),
    ).toBeVisible();
    await pending
      .getByRole("button", {
        name: "Отклонить без изменения рукописи",
        exact: true,
      })
      .click();
    await expect(editor.getByRole("status")).toContainText("оригинал сохранён");
    await expect(
      editor.getByRole("textbox", { name: "Текст главы", exact: true }),
    ).toHaveValue("Старая проза о море.");
    // A real UI conversion uses only selected chat messages and writes a new book.
    const headers = { "X-Taverna-CSRF": csrf };
    const character = (
      await (await page.request.get(appUrl + "/api/v1/characters/new")).json()
    ).character;
    character.fields.name = "Моряк";
    character.fields.first_mes = "Моряк вернулся в гавань.";
    const npc = await (
      await page.request.post(appUrl + "/api/v1/characters", {
        headers,
        data: { expected_revision: 0, character },
      })
    ).json();
    const story = await (
      await page.request.post(appUrl + "/api/v1/stories", {
        headers,
        data: {
          name: "Исходный чат для книги",
          character_ids: [npc.id],
          greeting_index: 0,
        },
      })
    ).json();
    const originalMessages = await (
      await page.request.get(appUrl + `/api/v1/stories/${story.id}/messages`)
    ).json();
    const originalBranch = (
      await (
        await page.request.get(appUrl + `/api/v1/stories/${story.id}`)
      ).json()
    ).branch;
    await editor
      .getByRole("button", {
        name: "Выбрать историю для преобразования",
        exact: true,
      })
      .click();
    await editor
      .getByRole("combobox", { name: "Источник", exact: true })
      .selectOption(story.id);
    await editor
      .getByRole("checkbox", { name: "Моряк вернулся в гавань.", exact: true })
      .check();
    await editor
      .getByRole("button", {
        name: "Предпросмотр преобразования выбранных сообщений",
        exact: true,
      })
      .click();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(appUrl + "/api/v1/manuscripts/proposals")
            ).json()
          ).items.length,
      )
      .toBe(2);
    const convertedProposal = (
      await (
        await page.request.get(appUrl + "/api/v1/manuscripts/proposals")
      ).json()
    ).items.find(
      (p: { payload: { request: { operation: string } } }) =>
        p.payload.request.operation === "chat_to_prose",
    );
    const converted = editor
      .locator("details")
      .filter({
        has: page
          .locator("summary")
          .filter({ hasText: convertedProposal.id.slice(0, 8) }),
      });
    await converted.locator("summary").first().click();
    await converted
      .getByRole("button", {
        name: "Подтвердить и применить эту правку",
        exact: true,
      })
      .click();
    await expect(
      editor.getByRole("textbox", { name: "Текст главы", exact: true }),
    ).toHaveValue("Связная новая проза о море.");
    expect(
      await (
        await page.request.get(appUrl + `/api/v1/stories/${story.id}/messages`)
      ).json(),
    ).toEqual(originalMessages);
    expect(
      (
        await (
          await page.request.get(appUrl + `/api/v1/stories/${story.id}`)
        ).json()
      ).branch,
    ).toEqual(originalBranch);
    await editor
      .getByRole("button", { name: "Море и 🌊 · версия 2", exact: true })
      .click();
    await editor.getByText("Версии и экспорт", { exact: true }).click();
    const files: Record<string, string> = {};
    for (const [label, name] of [
      ["TXT", "book.txt"],
      ["Markdown + медиа ZIP", "book-markdown.zip"],
      ["HTML", "book.html"],
      ["EPUB", "book.epub"],
    ]) {
      const wait = page.waitForEvent("download");
      await editor
        .getByRole("button", { name: "Скачать " + label, exact: true })
        .click();
      const d = await wait;
      expect(d.suggestedFilename()).toBe(name);
      files[name] = (await d.path())!;
    }
    expect(readFileSync(files["book.txt"], "utf8")).toContain("Море и 🌊");
    await editor
      .getByRole("button", { name: "Показать историю изменений", exact: true })
      .click();
    await expect(
      editor.getByRole("button", {
        name: "Восстановить в новую версию",
        exact: true,
      }),
    ).toHaveCount(2);
    await editor
      .getByRole("button", { name: "Восстановить в новую версию", exact: true })
      .last()
      .click();
    await expect(
      editor.getByRole("textbox", { name: "Текст главы", exact: true }),
    ).toHaveValue("Моряк вошёл в порт.\n\nОн увидел море.");
    await editor
      .getByText("JSON рукописи и незавершённый ввод", { exact: true })
      .click();
    await editor
      .getByLabel("JSON рукописи", { exact: true })
      .fill('{"chapters": [');
    await expect(
      editor.getByText("Черновик рукописи автосохранён.", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await page
      .getByRole("button", { name: "Литературный редактор", exact: true })
      .click();
    await editor.getByText("Черновики рукописи", { exact: true }).click();
    await editor
      .getByRole("button", { name: /Восстановить черновик/ })
      .first()
      .click();
    await editor
      .getByText("JSON рукописи и незавершённый ввод", { exact: true })
      .click();
    await expect(
      editor.getByLabel("JSON рукописи", { exact: true }),
    ).toHaveValue('{"chapters": [');
    expect(calls.some((c) => /load|unload|download/.test(c))).toBe(false);
    const jar = resolve("../../.test-tools/epubcheck-5.4.0/epubcheck.jar");
    if (existsSync(jar)) {
      const checked = files["book.epub"] + ".epub";
      copyFileSync(files["book.epub"], checked);
      const result = execFileSync("java", ["-jar", jar, checked], {
        encoding: "utf8",
        timeout: 60000,
      });
      expect(result).toContain("No errors or warnings");
    }
  } finally {
    server.closeAllConnections();
    await new Promise<void>((done) => server.close(() => done()));
  }
});

test("EPUB: настоящая читалка epub.js открывает главы и встроенное изображение", async ({
  page,
  appUrl,
  context,
}) => {
  const toolRoot = resolve("../../.test-tools");
  test.skip(
    !existsSync(resolve(toolRoot, "epubjs/epub.min.js")),
    "Run scripts/prepare_epub_tools.py; external reader was not installed",
  );
  const csrf = (
    await (await page.request.get(appUrl + "/api/v1/session")).json()
  ).csrf;
  const headers = { "X-Taverna-CSRF": csrf };
  const image = await (
    await page.request.post(
      appUrl +
        "/api/v1/media/import?name=Reader&category=illustration&confirmed=true",
      {
        headers,
        data: readFileSync(
          resolve("../../tests/fixtures/media/background.png"),
        ),
      },
    )
  ).json();
  const mid = crypto.randomUUID(),
    first = crypto.randomUUID(),
    second = crypto.randomUUID();
  const payload = {
    format: 1,
    title: "Проверка читалки",
    author: "Таверна",
    language: "ru",
    chapters: [
      {
        id: first,
        title: "Гавань",
        text: "Кириллица в настоящей читалке.",
        links: [{ label: "Далее", target: "#chapter-" + second }],
        media: [{ asset_id: image.id, caption: "Гавань на рисунке" }],
      },
      {
        id: second,
        title: "Маяк",
        text: "Вторая глава открылась.",
        links: [],
        media: [],
      },
    ],
  };
  const saved = await page.request.put(appUrl + "/api/v1/manuscripts/" + mid, {
    headers,
    data: { expected_revision: 0, payload, reason: "Reader trial" },
  });
  expect(saved.ok()).toBe(true);
  const epub = await (
    await page.request.get(
      appUrl +
        `/api/v1/manuscripts/${mid}/export?format=epub&expected_revision=1`,
    )
  ).body();
  const reader = await context.newPage();
  await reader.route("http://epub-reader.test/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/")
      return route.fulfill({
        contentType: "text/html",
        body: '<!doctype html><html><body><div id="reader" style="width:800px;height:700px"></div><script src="/zip.js"></script><script src="/epub.js"></script></body></html>',
      });
    if (path === "/zip.js")
      return route.fulfill({
        contentType: "text/javascript",
        body: readFileSync(resolve(toolRoot, "jszip/jszip.min.js")),
      });
    if (path === "/epub.js")
      return route.fulfill({
        contentType: "text/javascript",
        body: readFileSync(resolve(toolRoot, "epubjs/epub.min.js")),
      });
    return route.abort();
  });
  await reader.goto("http://epub-reader.test/");
  const result = await reader.evaluate(async (encoded) => {
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    const w = window as unknown as {
      ePub: (data: ArrayBuffer) => any;
      book: any;
      rendition: any;
    };
    w.book = w.ePub(bytes.buffer);
    await w.book.ready;
    w.rendition = w.book.renderTo("reader", {
      width: 800,
      height: 700,
      flow: "scrolled-doc",
      allowScriptedContent: false,
    });
    await w.rendition.display();
    return {
      title: w.book.packaging.metadata.title,
      toc: w.book.navigation.toc.map((v: { label: string }) => v.label),
    };
  }, epub.toString("base64"));
  expect(result.title).toBe("Проверка читалки");
  expect(result.toc).toEqual(["Гавань", "Маяк"]);
  const frame = reader.frameLocator("iframe");
  await expect(frame.getByText("Кириллица в настоящей читалке.")).toBeVisible();
  await expect
    .poll(() =>
      frame
        .locator("img")
        .evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth),
    )
    .toBe(64);
  await reader.evaluate(async () => {
    const w = window as unknown as { rendition: any; book: any };
    await w.rendition.display(w.book.navigation.toc[1].href);
  });
  await expect(frame.getByText("Вторая глава открылась.")).toBeVisible();
  await reader.close();
});
