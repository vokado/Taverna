"""Windows manual-update wizard. Original installation stays untouched."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from taverna.core import AppError
from taverna.full_backup import restore_full_backup_to_new_directory
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


def select_restore():
    """Restore only to a new, previously nonexistent folder."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showinfo(
            "Таверна — восстановление",
            "Закройте Таверну. Архив будет проверен и распакован только в "
            "НОВУЮ папку. Существующие данные не заменяются.",
            parent=root,
        )
        archive = filedialog.askopenfilename(
            title="Выберите полную резервную копию ZIP",
            filetypes=[("ZIP", "*.zip")], parent=root,
        )
        if not archive:
            return None
        parent = filedialog.askdirectory(
            title="Выберите расположение нового каталога восстановления",
            parent=root,
        )
        if not parent:
            return None
        name = simpledialog.askstring(
            "Новый каталог",
            "Имя новой папки для восстановленных данных:",
            parent=root,
        )
        if not name:
            return None
        if (
            name in (".", "..")
            or name.endswith((" ", "."))
            or not re.fullmatch(r"[\\w .()\\-]{1,100}", name)
        ):
            messagebox.showerror("Ошибка", "Недопустимое имя папки.")
            return None
        return Path(archive), Path(parent) / name
    finally:
        root.destroy()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ручное обновление Таверны")
    parser.add_argument("--source")
    parser.add_argument("--archive")
    parser.add_argument("--destination")
    parser.add_argument("--sha256")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--restore-archive")
    parser.add_argument("--restore-destination")
    args = parser.parse_args(argv)

    restore = args.restore or bool(args.restore_archive or args.restore_destination)
    if not any((args.source, args.archive, args.destination, args.restore_archive, args.restore_destination)):
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            answer = messagebox.askyesnocancel(
                "Выбор действия",
                "Восстановить данные из резервного ZIP?\\n"
                "Да — восстановление в новую папку.\\n"
                "Нет — обновление программы.",
                parent=root,
            )
        finally:
            root.destroy()
        if answer is None:
            return 0
        restore = bool(answer)
    if restore:
        cli_restore = bool(args.restore_archive and args.restore_destination)
        chosen_restore = (
            (Path(args.restore_archive), Path(args.restore_destination))
            if cli_restore
            else select_restore()
        )
        if chosen_restore is None:
            return 0
        try:
            output = restore_full_backup_to_new_directory(*chosen_restore)
        except (AppError, OSError, ValueError) as exc:
            message = str(exc) if isinstance(exc, AppError) else "Восстановление не выполнено."
            if cli_restore:
                if sys.stderr is not None:
                    print(message.encode("ascii", "backslashreplace").decode("ascii"), file=sys.stderr)
            else:
                from tkinter import messagebox

                messagebox.showerror("Ошибка восстановления", message)
            return 1
        if cli_restore:
            if sys.stdout is not None:
                print(json.dumps({"restored_to": str(output)}, ensure_ascii=True))
        else:
            from tkinter import messagebox

            messagebox.showinfo(
                "Восстановление завершено",
                "Архив проверен и восстановлен в новый каталог:\\n"
                + str(output)
                + "\\nСуществующая установка не изменена.",
            )
        return 0
    cli = all((args.source, args.archive, args.destination))
    chosen = (
        (Path(args.source), Path(args.archive), Path(args.destination), args.sha256)
        if cli
        else select_paths()
    )
    if chosen is None:
        return 0
    try:
        def report_phase(phase: str):
            if cli and sys.stderr is not None:
                print("UPDATER_PHASE:", phase, file=sys.stderr, flush=True)

        outcome = prepare_update(
            *chosen[:3],
            expected_sha256=chosen[3],
            progress=report_phase,
        )
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
            print(json.dumps(outcome, ensure_ascii=True))
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
