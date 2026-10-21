import { test, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

test("offline library searches full notes and includes earlier batches without hiding versions", async ({
  page,
}) => {
  // Explicit synthetic UI fixture; semantic model accuracy is outside this test.
  const root = resolve("../work/library-browser-fixture");
  await mkdir(root, { recursive: true });
  const entries = ["图标课程", "字体课程", "后续条件补充"].map((title, i) => ({
    entry_id: `video-${i}/run-${i}-v1`,
    asset_id: `video-${i}`,
    title,
    run_id: `run-${i}`,
    document_version: 1,
    kind: i === 0 ? "historical_note" : "workflow_snapshot",
    knowledge_count: i === 0 ? null : 2,
    review_status:
      i === 0
        ? "full_coverage_key_evidence_reviewed"
        : "generated_awaiting_source_review",
    created_at: i,
    search_text: `${title}：${i === 0 ? "负形与视觉重量" : "适用条件与案例"}`,
    note_url: `note-${i}.html`,
    markdown_url: `note-${i}.md`,
    knowledge_url: i === 0 ? null : `knowledge-${i}.json`,
    original_note_url: i === 0 ? "note-0.html" : null,
    source_url: i === 0 ? "manifest-0.json" : null,
    manifest_url: `manifest-${i}.json`,
  }));
  for (let i = 0; i < entries.length; i++) {
    await writeFile(
      resolve(root, `note-${i}.html`),
      `<meta charset="utf-8"><h1>${entries[i].title}</h1><p>完整独立笔记</p>`,
    );
    await writeFile(resolve(root, `note-${i}.md`), entries[i].search_text);
    await writeFile(resolve(root, `knowledge-${i}.json`), "{}");
    await writeFile(resolve(root, `manifest-${i}.json`), "{}");
  }
  const batches = [
    {
      id: "b1",
      label: "第一批",
      entry_ids: entries.slice(0, 2).map((e) => e.entry_id),
      inherited_entry_ids: [],
      created_at: 1,
      status: "complete",
    },
    {
      id: "b2",
      label: "第二批",
      entry_ids: [entries[2].entry_id],
      inherited_entry_ids: entries.slice(0, 2).map((e) => e.entry_id),
      created_at: 2,
      status: "complete",
    },
  ];
  const template = await readFile(
    resolve("../backend/video_tutor/templates/learning-library.html"),
    "utf8",
  );
  await writeFile(
    resolve(root, "index.html"),
    template.replace(
      "__LIBRARY_DATA__",
      JSON.stringify({ entries, batches }).replace(/</g, "\\u003c"),
    ),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(pathToFileURL(resolve(root, "index.html")).href);
  await expect(page.locator("article")).toHaveCount(3);
  await page.getByLabel("学习批次").selectOption("b1");
  await expect(page.locator("article")).toHaveCount(2);
  await page.getByLabel("学习批次").selectOption("b2");
  await expect(page.locator("article")).toHaveCount(3);
  await page.getByLabel("搜索标题、知识点或笔记正文").fill("负形");
  await expect(page.locator("article")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "图标课程", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("原记录：已完成关键证据审阅")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "原笔记", exact: true }),
  ).toHaveAttribute("href", "note-0.html");
  await expect(
    page.getByRole("link", { name: "视频来源与审阅记录", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "知识清单", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "阅读笔记 →" }).click();
  await expect(page.getByText("完整独立笔记")).toBeVisible();
  await page.goBack();
  await page.getByLabel("搜索标题、知识点或笔记正文").fill("不存在的内容");
  await expect(
    page.getByText("没有匹配的笔记。试试其他关键词或选择全部批次。"),
  ).toBeVisible();
  await page.getByLabel("搜索标题、知识点或笔记正文").fill("");
  await page.screenshot({
    path: "../work/library-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "../work/library-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
