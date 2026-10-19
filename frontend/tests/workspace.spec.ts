import { test, expect } from "@playwright/test";
const settings = {
  directories: [],
  auto_import: true,
  require_ac_power: true,
  language: "auto",
  profile: "balanced",
  asr_backend: "whisper_cpp",
  glossary: [],
  frames_per_minute: 24,
  vision_model: "qwen3-vl:8b",
  text_model: "qwen3:14b",
  whisper_model_path: "/models/whisper.bin",
};
const asset = {
  id: "v1",
  title: "设置教程",
  size_bytes: 1000000,
  created_at: 1700000000,
  run_id: "r1",
  status: "succeeded_with_warnings",
  stage: "complete",
  media_json: '{"duration_ms":10000}',
};
const document = {
  version: 1,
  body: {
    source_title: "设置教程",
    duration_ms: 10000,
    overview: {
      title: "端口配置教程",
      summary: "将端口设置为 8080 并保存。",
      summary_evidence_ids: ["t1"],
      goals: [],
      prerequisites: [],
      key_points: [],
      verification: [],
      unresolved_items: [],
    },
    warnings: ["测试资料"],
    chapters: [
      {
        title: "修改端口",
        summary: "展示配置过程",
        steps: [
          {
            title: "填写端口",
            action: "将端口输入框设置为 8080",
            start_ms: 2000,
            end_ms: 4000,
            evidence_ids: ["t1"],
            evidence_type: "speech",
            parameters: [],
            uncertainties: [],
          },
        ],
        unresolved_items: [],
      },
    ],
    transcript: [
      { id: "t1", start_ms: 2000, end_ms: 4000, raw_text: "输入端口8080" },
    ],
    frames: {},
    user_notes: "",
  },
};
async function mock(page, hasVideo = false) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace("/api/v1", "");
    let data: any = { ok: true };
    if (p === "/assets") data = hasVideo ? [asset] : [];
    else if (p === "/health") data = { worker_alive: true, waiting_reason: "" };
    else if (p === "/settings") data = settings;
    else if (p === "/doctor")
      data = { checks: [{ name: "视觉模型", ok: true, detail: "已安装" }] };
    else if (p === "/runs/r1")
      data = { id: "r1", status: "succeeded_with_warnings", stage: "complete" };
    else if (p.endsWith("/events")) data = [];
    else if (p.endsWith("/documents/latest")) data = document;
    else if (p === "/search")
      data = [
        {
          id: 1,
          asset_id: "v1",
          run_id: "r1",
          title: "设置教程",
          start_ms: 2000,
          raw_text: "端口8080",
        },
      ];
    await route.fulfill({ json: data });
  });
}
test("empty workspace and directory import", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "让每段视频，成为你的知识。" }),
  ).toBeVisible();
  await page.screenshot({ path: "../work/ui-library.png", fullPage: true });
  await page.getByRole("button", { name: "导入视频", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByPlaceholder("例如 /Users/你的名字/Movies/教程")
    .fill("/tmp/videos");
  await page.getByRole("button", { name: "添加并扫描" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
});
test("notes, transcript and explicit evidence", async ({ page }) => {
  await mock(page, true);
  await page.goto("/");
  await page.getByRole("button", { name: "设置教程", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "端口配置教程" }),
  ).toBeVisible();
  await expect(page.getByText("将端口输入框设置为 8080")).toBeVisible();
  await page.getByRole("button", { name: "完整字幕" }).click();
  await expect(page.getByText("输入端口8080")).toBeVisible();
  await page.screenshot({ path: "../work/ui-detail.png", fullPage: true });
});
test("settings and narrow viewport", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await page.getByRole("button", { name: "偏好设置" }).click();
  await expect(
    page.getByRole("heading", { name: "按你的节奏学习" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(
    page.getByText("设置已保存，新的任务会使用这些设置"),
  ).toBeVisible();
  await page.setViewportSize({ width: 600, height: 900 });
  await page.screenshot({ path: "../work/ui-mobile.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
