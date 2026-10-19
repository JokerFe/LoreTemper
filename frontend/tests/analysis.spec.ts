import { test, expect } from "@playwright/test";
const hour = {
  total_tokens: 632243,
  usage: {
    input_tokens: 597526,
    cached_input_tokens: 212746,
    output_tokens: 34717,
  },
  multiplier: 1,
  api_equivalent_usd: 1.138,
  caveat: "estimated",
};
const defaults = {
  provider: "codex",
  model: "runtime-sol",
  reasoning: "medium",
  service_tier: "default",
  vision_model: "local-vl",
  text_model: "local-text",
  output_directory: "/tmp/学习成果",
};
const models = [
  {
    model: "runtime-sol",
    displayName: "动态 Sol",
    cost_multiplier: 1,
    isDefault: true,
    inputModalities: ["text", "image"],
    supportedReasoningEfforts: [
      { reasoningEffort: "medium" },
      { reasoningEffort: "high" },
    ],
    serviceTiers: [{ id: "priority", name: "Fast" }],
    hour_estimate: hour,
  },
  {
    model: "runtime-new",
    displayName: "新出现的模型",
    cost_multiplier: null,
    inputModalities: ["text", "image"],
    defaultReasoningEffort: "high",
    supportedReasoningEfforts: [{ reasoningEffort: "high" }],
    hour_estimate: { ...hour, multiplier: null, api_equivalent_usd: null },
  },
];

test("dynamic model selection, native bulk files, estimates and frozen job submission", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | null = null;
  let refreshes = 0;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    let json: unknown = {};
    if (path === "/assets") json = [];
    if (path === "/health") json = { worker_alive: true };
    if (path === "/analysis/defaults") json = defaults;
    if (path === "/analysis/jobs") {
      if (route.request().method() === "POST")
        submitted = route.request().postDataJSON();
      json = route.request().method() === "POST" ? { batch_id: "b1" } : [];
    }
    if (path === "/analysis/models") {
      refreshes++;
      json =
        url.searchParams.get("provider") === "ollama"
          ? {
              logged_in: true,
              source: "Ollama",
              models: [
                {
                  model: "local-vl",
                  displayName: "local-vl",
                  inputModalities: ["text", "image"],
                },
                {
                  model: "local-text",
                  displayName: "local-text",
                  inputModalities: ["text"],
                },
              ],
            }
          : { logged_in: true, source: "本机 Codex", models };
    }
    if (path === "/analysis/pick")
      json = {
        paths:
          route.request().postDataJSON().kind === "output"
            ? ["/tmp/新的输出"]
            : ["/tmp/课程一.mp4", "/tmp/课程二.mp4"],
      };
    if (path === "/analysis/preview")
      json = {
        files: [
          { path: "/tmp/课程一.mp4", name: "课程一.mp4", duration_ms: 3600000 },
          { path: "/tmp/课程二.mp4", name: "课程二.mp4", duration_ms: 3600000 },
        ],
        errors: [],
        duration_ms: 7200000,
        estimate: { ...hour, total_tokens: 1264486, api_equivalent_usd: 2.276 },
      };
    await route.fulfill({ json });
  });
  await page.goto("/?view=study-advanced");
  await expect(
    page.getByRole("heading", { name: "把好内容，变成自己的知识。" }),
  ).toBeVisible();
  await expect(page.getByLabel("分析模型")).toHaveValue("runtime-sol");
  await page.getByLabel("分析模型").selectOption("runtime-new");
  await expect(page.getByLabel("思考强度")).toHaveValue("high");
  await expect(page.locator(".estimate-multiplier strong")).toHaveText(
    "待估算",
  );
  await page.getByLabel("刷新模型").click();
  await expect.poll(() => refreshes).toBeGreaterThan(1);
  await page.getByLabel("分析模型").selectOption("runtime-sol");
  await page.getByLabel("处理速度").selectOption("priority");
  await expect(page.locator(".estimate-multiplier strong")).toHaveText("2×");
  await page.screenshot({
    path: "../work/ui-analysis-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Ollama 本地模型" }).click();
  await expect(page.getByLabel("视觉模型")).toHaveValue("local-vl");
  await expect(page.getByLabel("文本模型")).toHaveValue("local-text");
  await expect(page.locator(".estimate-multiplier strong")).toHaveText("0×");
  await page.getByRole("button", { name: "Codex 订阅" }).click();
  await expect(page.getByLabel("分析模型")).toHaveValue("runtime-sol");
  await page.getByRole("button", { name: "选择视频文件", exact: true }).click();
  await expect(page.getByText("已识别 2 个视频")).toBeVisible();
  await page.getByRole("button", { name: "浏览", exact: true }).click();
  await expect(page.getByLabel("输出文件夹", { exact: true })).toHaveValue(
    "/tmp/新的输出",
  );
  await expect(
    page.getByRole("button", { name: "开始学习", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "开始学习", exact: true }).click();
  await expect(page.getByText("已将 2 个视频加入学习队列")).toBeVisible();
  expect(submitted).toMatchObject({
    provider: "codex",
    model: "runtime-sol",
    output_directory: "/tmp/新的输出",
    vision_model: "local-vl",
    text_model: "local-text",
    paths: ["/tmp/课程一.mp4", "/tmp/课程二.mp4"],
  });
  await page.setViewportSize({ width: 600, height: 900 });
  await page.screenshot({
    path: "../work/ui-analysis-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("model service failure disables start and gives refresh path", async ({
  page,
}) => {
  await page.route("**/api/v1/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p.endsWith("/analysis/models"))
      return route.fulfill({
        status: 503,
        json: { detail: "Codex 模型服务不可用" },
      });
    await route.fulfill({
      json:
        p.endsWith("/assets") || p.endsWith("/analysis/jobs")
          ? []
          : p.endsWith("/analysis/defaults")
            ? defaults
            : {},
    });
  });
  await page.goto("/?view=study-advanced");
  await expect(page.getByText("Codex 模型服务不可用")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始学习", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("刷新模型")).toBeEnabled();
});
