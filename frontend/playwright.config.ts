import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8876",
    headless: true,
    channel: "chrome",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: "../.venv/bin/python -m video_tutor serve --port 8876",
    url: "http://127.0.0.1:8876",
    reuseExistingServer: false,
    timeout: 30000,
    env: { VIDEO_TUTOR_DATA: "../work/ui-test-data" },
  },
  reporter: "list",
  outputDir: "../work/playwright-results",
});
