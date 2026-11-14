# Таверна 1.0

Русскоязычное локальное приложение для Windows с интеграцией LM Studio.

Текущий статус: разработка этапов 01–16 разрешена владельцем 15 ноября 2026. Это внутренняя сборка, полный релиз 1.0 ещё не готов. Текущее состояние и недоступные испытания — в `reports/STATUS.md`.

## Запуск для разработки

Python 3.12+, Node 22+:

```sh
python -m venv .venv
python -m pip install -r requirements.lock
python -m pip install -e .
cd app/web
npm ci
npm run build
cd ../..
python -m taverna.launcher
```

Перед командами активируйте `.venv` (`.venv\Scripts\Activate.ps1` в Windows). Пользовательская portable-сборка будет включать runtime; эти зависимости нужны только разработчику. Windows build: `packaging/build_windows.ps1` после сборки UI. Процедура инициализации/защиты описана в `docs/ARCHITECTURE.md`.

Подготовительный пакет для согласования находится в ветке [`docs/preparation-1.0`](https://github.com/vokado/Taverna/tree/docs/preparation-1.0).

[Скачать архив документов](https://github.com/vokado/Taverna/raw/refs/heads/docs/preparation-1.0/Taverna_1_0_Preparation.zip)

Архив содержит AGENTS.md, PLANS.md, аудит, матрицу требований, вопросы для согласования, результаты проверки документов и оба исходных документа. Это документация, а не сборка приложения.

SHA-256 архива: `f9b6aced5549e23c30d85b678a97f798f923ef3029192a4a4d858d619beeda6a`.
