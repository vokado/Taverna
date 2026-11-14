import {test, expect} from '@playwright/test';
import {mkdtempSync, mkdirSync, cpSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve, join} from 'node:path';
import {spawn} from 'node:child_process';

test('Одноразовый вход, перезагрузка и закрытие вкладки', async ({page, context}) => {
  const root = mkdtempSync(join(tmpdir(), 'taverna-e2e-'));
  mkdirSync(join(root, 'app/web'), {recursive:true});
  cpSync(resolve('dist'), join(root, 'app/web/dist'), {recursive:true});
  const proc = spawn(process.env.TAVERNA_PYTHON || 'python', ['-m','taverna.launcher','--root',root,'--no-browser'], {stdio:'ignore'});
  let base = '';
  try {
    await expect.poll(async () => {
      try {
        const info = JSON.parse(readFileSync(join(root, 'data/config/instance.json'),'utf8'));
        base = `http://127.0.0.1:${info.port}`;
        return (await fetch(base + '/api/v1/health')).status;
      } catch {return 0;}
    }).toBe(200);
    const key = readFileSync(join(root, 'data/config/launcher.key'), 'utf8');
    const launch = await fetch(base + '/api/v1/launcher/open', {method:'POST',headers:{'X-Taverna-Launcher':key}});
    const {url} = await launch.json();
    await page.goto(url);
    await expect(page.getByRole('heading',{name:'Таверна',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'Завершить приложение'})).toBeVisible();
    expect(page.url()).not.toContain('launch=');
    await page.reload();
    await expect(page.getByRole('button',{name:'Завершить приложение'})).toBeVisible();
    await page.close();
    expect((await fetch(base + '/api/v1/health')).status).toBe(200);
    const other = await context.newPage();
    await other.goto(base);
    other.on('dialog', dialog => dialog.accept());
    await other.getByRole('button',{name:'Завершить приложение'}).click();
    await expect(other.getByRole('status')).toContainText('завершает работу');
    await expect.poll(() => proc.exitCode, {timeout:10000}).toBe(0);
  } finally {
    if (proc.exitCode === null) {proc.kill(); await new Promise(r=>proc.once('exit',r));}
    rmSync(root, {recursive:true,force:true});
  }
});
