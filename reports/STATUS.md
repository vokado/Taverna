# Состояние разработки

Допуск на 01–16: получен 2026-11-15. D-01–15: APPROVED.

| Этап | Реализация / проверки |
|---|---|
| 01 | Доступные Linux unit/process/browser/build проверки PASS; Windows EXE/clean portable пока BLOCKED/NOT_RUN |
| 02 | Foundation реализован; 22 backend tests (с регрессией) PASS, browser regression PASS; Windows CI повторяется |
| 03 | IN_PROGRESS |
| 04–16 | NOT_STARTED |

Первый официальный 1.0 не готов. Неполные внутренние сборки не являются релизом.

Аппаратные блокировки: локально доступен Linux, Windows/Wine/RTX5070/LM Studio не обнаружены. Windows workflow подготовлен; результаты CI не считать PASS до чтения фактического запуска. Реальная модель и генератор изображений не подменяются тестовыми doubles.
