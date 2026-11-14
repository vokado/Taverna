import {defineConfig} from '@playwright/test';
import {existsSync} from 'node:fs';
export default defineConfig({
  testDir: './e2e', timeout: 30000, workers: 1,
  use: {headless: true, launchOptions: existsSync('/usr/bin/chromium') ? {executablePath: '/usr/bin/chromium'} : {}, screenshot: 'only-on-failure', trace: 'retain-on-failure'},
});
