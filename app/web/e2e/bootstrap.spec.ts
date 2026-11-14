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
    await expect(page.getByRole('heading',{name:'Ваша Таверна',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'Завершить приложение'})).toBeVisible();
    expect(page.url()).not.toContain('launch=');
    await page.reload();
    await expect(page.getByRole('button',{name:'Завершить приложение'})).toBeVisible();
    await page.getByRole('button',{name:'Настройки',exact:true}).click();
    await page.getByLabel('Уровень настроек').selectOption('expert');
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.getByLabel('Дополнительные экспертные значения (JSON)').fill('{"seed":42}');
    await page.getByRole('button',{name:'Сохранить экспертные значения',exact:true}).click();
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.getByLabel('Уровень настроек').selectOption('simple');
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.getByRole('combobox',{name:'Тема',exact:true}).selectOption('warm');
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.reload();
    await page.getByRole('button',{name:'Настройки',exact:true}).click();
    await expect(page.getByRole('combobox',{name:'Тема',exact:true})).toHaveValue('warm');
    await page.getByLabel('Уровень настроек').selectOption('expert');
    await expect(page.getByLabel('Дополнительные экспертные значения (JSON)')).toHaveValue(/42/);
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.getByRole('button',{name:'Рабочее пространство',exact:true}).click();
    await page.getByLabel('Название нового пространства').fill('Проверочное пространство');
    await page.getByRole('button',{name:'Сохранить как новое пространство'}).click();
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.getByRole('button',{name:'Свернуть',exact:true}).first().click();
    await expect(page.locator('.save-state')).toHaveText('Все изменения сохранены.');
    await page.reload();
    await page.getByRole('button',{name:'Рабочее пространство',exact:true}).click();
    await expect(page.getByRole('button',{name:'Развернуть',exact:true})).toBeVisible();
    for(const width of [1920,1280,640]){
      await page.setViewportSize({width,height:720});
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
      await expect(page.getByRole('button',{name:'Завершить приложение'})).toBeVisible();
    }
    await page.close();
    expect((await fetch(base + '/api/v1/health')).status).toBe(200);
    const other = await context.newPage();
    await other.goto(base);
    other.on('dialog', dialog => dialog.accept());
    await other.getByRole('button',{name:'Завершить приложение'}).click();
    await expect(other.getByRole('status').filter({hasText:'Таверна завершает'})).toContainText('завершает работу');
    await expect.poll(() => proc.exitCode, {timeout:10000}).toBe(0);
  } finally {
    if (proc.exitCode === null) {proc.kill(); await new Promise(r=>proc.once('exit',r));}
    rmSync(root, {recursive:true,force:true});
  }
});
