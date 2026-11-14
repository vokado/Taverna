# Этап 04 — UI и workspaces

2026-11-15. Реализована независимая основа UI: навигация, русские loading/empty/error states, реальный home summary, three-level settings без потери expert fields, темы/ручные цвета/font/size/economy, workspace documents, перемещение/размер/сворачивание панелей и immersion. Сохранение с revision/dirty/saving/error; при conflict нет скрытого overwrite. Данные UI не смешиваются с историями.

Проверки: backend regression + settings tests, строгая TypeScript/Vite сборка, browser E2E с reload/expert preservation/theme/workspace/collapse и 1920/1280/640 без horizontal overflow. Фактическое число backend PASS фиксируется после последнего прогона, ниже. Исходный e2e locator getByLabel exact для select не совпадал с label, содержащим option text; исправлен на getByRole combobox с accessible name. Ошибка locator не скрывалась увеличением timeout.

Открыто: реальное заполнение персонажных/chat/memory/world/director/media панелей зависит от будущих модулей. UI-001/004/005/008 и аспекты UI-007/009 не считаются целиком готовыми. Макет внутренний и может уточняться без потери функций. Полноценная library ещё не реализована. Нет production моков/фальшивых карточек.

CI предыдущего коммита 7277b59: run 43637361714 — Ubuntu SUCCESS и Windows SUCCESS, включая **actual frozen EXE smoke**: оба EXE, перенесённая папка с кириллицей/пробелами, PATH только System32, HTTP/session/storage/shutdown. Это подтверждает упаковочную технологию на Windows runner; целевая Windows11/Edge/Chrome/RTX5070/LM Studio остаётся отдельной проверкой.

REQ частично: UI-001–003/006–012, GEN-003, NFR-001/006, ARCH-004. Матрица сохраняет составные родительские требования открытыми до интеграции всех доменов. Доступных критических дефектов после проверок нет. Следующий независимый этап — 05, карточки/совместимость; live AI wizard остаётся зависимым от доступа к LM Studio.

Последний backend прогон: 36 PASS за 7.98s; ruff PASS. Browser workspace/regression: 1 PASS за 6.2s; TypeScript/Vite build PASS.
