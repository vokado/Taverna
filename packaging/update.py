"""Windows manual-update wizard. Original installation stays untouched."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from taverna.core import AppError
from taverna.manual_update import prepare_update


def select_paths():
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showinfo(
            "Таверна — обновление",
            "Закройте Таверну. Обновление создаст отдельную новую папку; "
            "старая установка и её данные сохранятся.",
            parent=root,
        )
        old = filedialog.askdirectory(title="Папка действующей Таверны", parent=root)
        if not old:
            return None
        archive = filedialog.askopenfilename(
            title="Новый ZIP-архив", filetypes=[("ZIP", "*.zip")], parent=root
        )
        if not archive:
            return None
        parent = filedialog.askdirectory(
            title="Расположение для новой установки", parent=root
        )
        if not parent:
            return None
        name = simpledialog.askstring(
            "Новая папка", "Имя новой папки (например, Taverna-1.0):", parent=root
        )
        if not name:
            return None
        if (
            name in (".", "..")
            or name.endswith((" ", "."))
            or not re.fullmatch(r"[\w .()\-]{1,100}", name)
        ):
            messagebox.showerror("Ошибка", "Укажите простое имя без разделителей.")
            return None
        sha = simpledialog.askstring(
            "SHA256", "Контрольная сумма нового ZIP (необязательно):", parent=root
        )
        return Path(old), Path(archive), Path(parent) / name, sha or None
    finally:
        root.destroy()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ручное обновление Таверны")
    parser.add_argument("--source")
    parser.add_argument("--archive")
    parser.add_argument("--destination")
    parser.add_argument("--sha256")
    args = parser.parse_args(argv)
    cli = all((args.source, args.archive, args.destination))
    chosen = (
        (Path(args.source), Path(args.archive), Path(args.destination), args.sha256)
        if cli
        else select_paths()
    )
    if chosen is None:
        return 0
    try:
        outcome = prepare_update(*chosen[:3], expected_sha256=chosen[3])
    except (AppError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, AppError) else "Обновление не выполнено."
        if cli:
            if sys.stderr is not None:
                print(message, file=sys.stderr)
        else:
            from tkinter import messagebox

            messagebox.showerror("Ошибка обновления", message)
        return 1
    if cli:
        if sys.stdout is not None:
            print(json.dumps(outcome, ensure_ascii=False))
    else:
        from tkinter import messagebox

        messagebox.showinfo(
            "Готово",
            "Новая установка создана в отдельном каталоге:\n"
            + outcome["destination"]
            + "\nСтарая версия сохранена и пригодна для возврата.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
