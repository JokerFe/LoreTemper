import { test, expect } from "@playwright/test";

test("independent and combined workflows preview concrete automatic configuration", async ({
  page,
}) => {
  const submitted: Record<string, unknown>[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (path === "/assets") data = [];
    if (path === "/health") data = { worker_alive: true };
    if (path === "/workflows/defaults")
      data = {
        video_paths: ["/videos"],
        note_paths: ["/notes/notes.md"],
        output_directory: "/output",
      };
    if (path === "/workflows/jobs") {
      if (route.request().method() === "POST") {
        submitted.push(route.request().postDataJSON());
        data = { id: "wf1" };
      } else data = [];
    }
    if (path === "/workflows/resolve") {
      const body = route.request().postDataJSON();
      const stage = {
        provider: "codex",
        model: "runtime-model",
        reasoning: "medium",
        service_tier: "default",
        vision_model: "local-vl",
        text_model: "local-text",
      };
      data = {
        output_directory: "/output",
        video_paths: body.mode === "skill" ? [] : ["/videos/chosen.mp4"],
        note_paths: body.mode === "skill" ? ["/notes/notes.md"] : [],
        study: body.mode === "skill" ? null : stage,
        skill:
          body.mode === "study" ? null : { ...stage, model: "another-model" },
        videos:
          body.mode === "skill"
            ? []
            : [{ name: "chosen.mp4", duration_ms: 3600000 }],
        notes:
          body.mode === "skill"
            ? [{ name: "notes.md", path: "/notes/notes.md", chars: 1000 }]
            : [],
        errors: [],
        estimates: {
          study:
            body.mode !== "skill"
              ? {
                  multiplier: 1,
                  total_tokens: 632243,
                  api_equivalent_usd: 1.14,
                }
              : undefined,
          skill:
            body.mode !== "study"
              ? {
                  token_range: [10000, 20000],
                  usd_range: [0.1, 0.3],
                  basis: "字符估算",
                }
              : undefined,
        },
      };
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=analysis");
  await expect(
    page.getByRole("heading", { name: "从视频到知识，从知识到 Skill。" }),
  ).toBeVisible();
  await expect(page.getByLabel("视频输入路径")).toBeVisible();
  await expect(page.getByLabel("笔记输入路径")).toHaveCount(0);
  await page
    .getByRole("button", { name: "笔记生成 Skill 已有笔记 → 可复用的 Skill" })
    .click();
  await expect(page.getByLabel("视频输入路径")).toHaveCount(0);
  await expect(page.getByLabel("笔记输入路径")).toBeVisible();
  await page.getByRole("button", { name: "自动配置并预览" }).click();
  await expect(page.getByText("1 份已有笔记", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "开始笔记生成 Skill", exact: true })
    .click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toMatchObject({
    mode: "skill",
    auto_input: false,
    note_paths: ["/notes/notes.md"],
    output_directory: "/output",
    skill: { model: "another-model", provider: "codex" },
  });
  await page
    .getByRole("button", { name: "学习并生成 Skill 视频 → 学习笔记 → Skill" })
    .click();
  await expect(
    page.getByText("本次视频生成的笔记会自动传给第二步，无需重复选择。"),
  ).toBeVisible();
  await page.getByLabel("视频输入路径").fill("/videos/first.mp4\n");
  await page.getByLabel("视频输入路径").press("End");
  await page.getByLabel("视频输入路径").type("/videos/second.mp4");
  await expect(page.getByLabel("视频输入路径")).toHaveValue(
    "/videos/first.mp4\n/videos/second.mp4",
  );
  await page.getByRole("button", { name: "自动配置并预览" }).click();
  await expect(
    page.getByText("0 份已有笔记 + 本次学习结果自动衔接", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../work/ui-workflows-combined.png",
    fullPage: true,
  });
  await page.getByLabel("Skill 用途").fill("仅指导课程中覆盖的图标设计");
  await expect(
    page.getByRole("button", { name: "开始学习并生成 Skill", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "自动配置并预览" }).click();
  await page
    .getByRole("button", { name: "开始学习并生成 Skill", exact: true })
    .click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1]).toMatchObject({
    mode: "combined",
    study: { model: "runtime-model" },
    skill: { model: "another-model" },
    skill_goal: "仅指导课程中覆盖的图标设计",
  });
  await page.setViewportSize({ width: 600, height: 900 });
  await page.screenshot({
    path: "../work/ui-workflows-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
