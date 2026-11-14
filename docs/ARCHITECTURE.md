# Архитектура: реализуемые контракты

Windows 11 x64, Edge/Chrome. Python 3.12/FastAPI, React 19/TypeScript/Vite. Внутренняя версия 0.1.dev; полный релиз 1.0 не готов.

## Запуск и сеанс

Запускатель удерживает portalocker на data/server.lock, резервирует socket только 127.0.0.1, пишет порт в instance.json. Отдельный launcher.key даёт локальному повторному запускателю право создать одноразовую ссылку, но не попадает в логи. На POSIX создаётся с mode 0600; на Windows права наследуются от portable-папки. Граница защиты: посторонние сайты; процессы с полным доступом к portable-папке уже могут читать/менять данные. Не рекомендуется хранить программу в общей доступной для записи папке. Исполняемые плагины не получают launcher.key через API.

Одноразовый 60-секундный токен передаётся во fragment URL: он не отправляется HTTP-серверу, frontend удаляет его через replaceState до обмена. Токен обменивается на HttpOnly/SameSite=Strict session cookie и CSRF, хранимый в памяти UI. Reload получает CSRF через same-origin authenticated GET. Origin/Host проверяются перед маршрутами, cross-site fetch блокируется. Никакого CORS. Сеанс истекает через 12 часов; повторный launcher выдаёт новую одноразовую ссылку. Секреты не включаются в обычные журналы.

Закрытие вкладки не завершает сервер. Явная кнопка завершения требует CSRF; uvicorn graceful shutdown прекращает listener и удаляет runtime metadata/key. Порт автоматический либо задан `--port`. При коллизии заданного порта запуск объясняет ошибку и не разделяет listener. Локальные тайны не являются защитой от другого процесса того же Windows-пользователя.

## HTTP

Маршруты `/api/v1`; схемы доступны аутентифицированному UI по `/api/v1/openapi.json`. Health не раскрывает личных данных. Валидация не отражает исходные секретные payload в ошибках. До dispatch тело ограничено, в том числе без Content-Length. Static CSP запрещает внешние scripts/connect/frame/формы, активный пользовательский HTML пока не принимается. Стабильные поля ошибки: code, message_ru, request_id, recoverable, details.

## Следующие модули

Данные только через будущий storage manager; JSON manifest поколения определяет согласованное чтение. Единая durable queue/outbox, identity story/branch/revision, proposal→validate→apply. Точные инварианты будут фиксироваться вместе с проверенными модулями, а не объявляться уже работающими.

## Сборка

`packaging/build_windows.ps1`: PyInstaller one-folder, основной EXE без консоли и диагностический console EXE, общий включённый runtime и static UI; внутренний ZIP. Windows workflow выполняет native build. Linux не создаёт валидированный Windows EXE. Перед полным 1.0 обязательны clean-Windows smoke, hardware/LM Studio/изображения, полный комплект docs/licenses и приёмка.
