"""Approximate budgets; neither estimator replaces the model's native tokenizer."""

from math import ceil


def estimate_text(text: str, method="balanced") -> int:
    size = len(text.encode("utf-8"))
    return size if method == "conservative" else ceil(size / 2)


def estimate_messages(messages: list[dict], method="balanced") -> int:
    return sum(estimate_text(str(message.get("content", "")), method) + 24 for message in messages)
