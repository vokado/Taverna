from __future__ import annotations

import logging

from .core import AppError


class EventBus:
    """One process-local notification bus; durable work remains on the existing JobQueue."""

    def __init__(self):
        self.subscribers = {}

    def subscribe(self, name, handler):
        if name in self.subscribers:
            raise ValueError("Подписчик уже зарегистрирован")
        self.subscribers[name] = handler

    async def __call__(self, story_id, branch_id, message_id, model_id=None):
        results = {}
        for name, handler in self.subscribers.items():
            try:
                results[name] = await handler(story_id, branch_id, message_id, model_id)
            except AppError as exc:
                results[name] = {"status": "failed", "code": exc.code, "message_ru": exc.message}
            except Exception as exc:
                logging.getLogger("taverna").error("Ошибка подписчика %s: %s", name, type(exc).__name__)
                results[name] = {
                    "status": "failed",
                    "code": "subscriber_failed",
                    "message_ru": "Вспомогательная обработка не завершена; реплика сохранена.",
                }
        return results
