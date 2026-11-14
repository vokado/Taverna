# LM Studio: адаптер и проверки

По умолчанию http://127.0.0.1:1234; пользователь может задать иной числовой loopback endpoint. Интернет/DNS/redirects/proxy credentials не используются. Адрес сохраняется JSON с expected_revision, каждый job фиксирует свой endpoint. Изменение подключения не перенаправляет уже поставленную задачу.

Каталог читается только `GET /api/v1/models` с models[].loaded_instances[].id/config. Незагруженные модели не показываются как доступные. Перед каждым inference повторяется проверка native loaded catalog; если API/состояние неизвестно, запрос отклоняется. В OpenAI Chat Completions передаётся instance id, никогда fallback на ключ незагруженного файла. Load/unload/download endpoints отсутствуют в коде. LM Studio остаётся под ручным управлением пользователя. Совместимость конкретных версий native API и гонка внешней ручной выгрузки с JIT подлежат живому тесту: тестовая схема каталога не доказывает поведение установленного LM Studio. До подтверждения не объявляется гарантия отключения JIT на стороне внешнего приложения.

Ответ: `/v1/chat/completions`, stateless messages, SSE data parsing, content/reasoning_content|reasoning раздельно, finish_reason/[DONE] для завершения. EOF без завершения — failed с сохранённым partial. Точный tokens/s только при реальном completion_tokens от сервера; stream chunks не считаются токенами. Оценка контекста — символы+overhead, явно приблизительная. Точный tokenizer и полный prompt budget — этап 07. Unknown structured output/tool calling/reasoning не объявляются поддержанными; reasoning учитывается только если поле реально пришло. Embedding-запрос только к подтверждённо загруженному embedding instance; ошибки сохраняют возможность текстового поиска будущего модуля.

Автоповтор: максимум одна повторная попытка только при ошибке установления соединения до событий. Read timeout/EOF/rejected request не повторяются автоматически. Повторная отправка с тем же Idempotency-Key возвращает тот же job; изменение payload с тем же key даёт conflict. Полноценный chat/сравнение/системные профили — этапы 06/07. Текущая UI-форма выполняет настоящий проверочный запрос к подключённому движку, не имитирует ответ.

## Устойчивая очередь и события

Одна bounded PriorityQueue (64 ожидающих) + durable JSON jobs/events/keys. Основной ответ priority=0, служебным обработчикам назначается меньший приоритет выполнения (большее число). Сейчас один выполняемый handler: тяжёлые задачи сериализованы. Cancel отменяет blocked handler/socket, а не ждёт следующего токена. Событие и увеличенный event_count публикуются одной storage transaction до SSE-выдачи. SSE id = job UUID:sequence; Last-Event-ID/after дают replay без повторного применения, frontend дедуплицирует sequence.

После перезапуска незавершённые jobs получают interrupted и сохранённое terminal event. Никакого слепого повторного inference. Частичные дельты доступны в «Сохранённых ответах и задачах» и API. Отдельные ошибки jobs не останавливают worker. Queue shutdown прерывает active handler; pending jobs корректно распознаются при следующем старте. Все дальнейшие maintenance/director/media задачи должны использовать этот queue contract; самостоятельные независимые очереди запрещены.

## Статус доказательств

На Linux пройдены tests/test_generation.py (MockTransport — только протокольные tests), unit/process/browser regression. Local 127.0.0.1:1234: connection refused; nvidia-smi отсутствует. Реального LM Studio/Dolphin/RTX5070 здесь нет. Попытки прочесть официальные страницы lmstudio.ai из среды получили HTTP403; достоверность новых версий native API не подтверждена. Живые модели, embedding vectors, reasoning-channel совместимость и hardware benchmarks: BLOCKED. Тестовые ответы не включены в production и не выдаются за живые результаты.
