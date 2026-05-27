#!/usr/bin/env python3
from __future__ import annotations

import queue
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parent


class WeautoGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("WeAuto Control")
        self.root.geometry("980x680")

        self.output_q: queue.Queue[str] = queue.Queue()
        self.running_proc: subprocess.Popen[str] | None = None

        self._build_ui()
        self._poll_output_queue()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Clear Output", command=self.clear_output).pack(side=tk.LEFT, padx=4)

        mid = ttk.LabelFrame(self.root, text="Calibration / Debug", padding=10)
        mid.pack(fill=tk.X, padx=10, pady=6)

        script_buttons = [
            ("Rows", "./carlibrate_rows.sh"),
            ("Chat Context", "./carlibrate_chat_context.sh"),
            ("Title Group", "./carlibrate_title_group.sh"),
            ("Title Private", "./carlibrate_title_private.sh"),
            ("Preview", "./carlibrate_preview.sh"),
            ("Unread", "./carlibrate_unread.sh"),
            ("Debug Click", "./debug_click.sh"),
            ("Debug Preview", "./debug_preview.sh"),
            ("Debug Unread", "./debug_unread.sh"),
            ("Debug Heartbeat", "./debug_heartbeat.sh"),
        ]

        for i, (label, cmd) in enumerate(script_buttons):
            ttk.Button(mid, text=label, command=lambda c=cmd: self.run_script(c)).grid(
                row=i // 5, column=i % 5, padx=4, pady=4, sticky="ew"
            )
        for col in range(5):
            mid.columnconfigure(col, weight=1)

        output_frame = ttk.LabelFrame(self.root, text="Output", padding=8)
        output_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.text = tk.Text(output_frame, wrap="word", font=("Menlo", 12))
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(output_frame, command=self.text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure("error", foreground="#c62828")
        self.text.tag_configure("warn", foreground="#ef6c00")
        self.text.tag_configure("ok", foreground="#2e7d32")
        self.text.tag_configure("meta", foreground="#546e7a")

    def append_line(self, line: str) -> None:
        line = line.rstrip("\n")
        tag = ""
        lower = line.lower()
        if "[warn]" in lower:
            tag = "warn"
        elif "[error]" in lower or "traceback" in lower:
            tag = "error"
        elif "[start]" in lower or "[sent]" in lower or "started" in lower:
            tag = "ok"
        elif line.startswith("$") or line.startswith("[gui]"):
            tag = "meta"

        self.text.insert(tk.END, line + "\n", tag)
        self.text.see(tk.END)

    def clear_output(self) -> None:
        self.text.delete("1.0", tk.END)

    def _poll_output_queue(self) -> None:
        try:
            while True:
                line = self.output_q.get_nowait()
                self.append_line(line)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_output_queue)

    def _run_cmd(self, cmd: str) -> None:
        def _worker() -> None:
            self.output_q.put(f"$ {cmd}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    shell=True,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                self.running_proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.output_q.put(line.rstrip("\n"))
                ret = proc.wait()
                self.output_q.put(f"[gui] command exit={ret}")
            except Exception as exc:
                self.output_q.put(f"[gui][error] run cmd failed: {exc}")
            finally:
                self.running_proc = None

        threading.Thread(target=_worker, daemon=True).start()

    def run_script(self, script_cmd: str) -> None:
        self._run_cmd(script_cmd)


def main() -> None:
    root = tk.Tk()
    app = WeautoGUI(root)
    app.append_line("[gui] WeAuto GUI ready")
    app.append_line(f"[gui] root={ROOT}")
    root.mainloop()


if __name__ == "__main__":
    main()
