from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
HISTORY_PATH = BASE_DIR / "move_history.json"

TEMP_SUFFIXES = {".tmp", ".crdownload", ".part", ".download", ".partial"}
IGNORED_NAMES = {"organizer.py", "config.json", "move_history.json"}
DEBOUNCE_SECONDS = 1.5
MAX_WORKERS = 4
MAX_RETRIES = 6
RETRY_BASE_SECONDS = 2
STABILITY_INTERVAL = 0.5
STABILITY_CHECKS = 3
STABILITY_TIMEOUT = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, list[str]] = {}
        self.mtime = -1.0
        self.reload()

    def reload(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Configuration must be a JSON object.")

            normalized: dict[str, list[str]] = {}
            for category, extensions in raw.items():
                if not isinstance(category, str) or not category.strip():
                    raise ValueError("Category names cannot be empty.")
                if not isinstance(extensions, list):
                    raise ValueError(f"Extensions for {category!r} must be a list.")

                clean_extensions = []
                for extension in extensions:
                    if not isinstance(extension, str):
                        raise ValueError("Extensions must be strings.")
                    extension = extension.strip().lower()
                    if extension:
                        clean_extensions.append(
                            extension if extension.startswith(".") else "." + extension
                        )
                normalized[category.strip()] = clean_extensions

            with self.lock:
                self.data = normalized
                self.mtime = self.path.stat().st_mtime
            log.info("Loaded %d categories.", len(normalized))

        except FileNotFoundError:
            log.warning("config.json not found; unknown extensions will use Others.")
            with self.lock:
                self.data = {}
                self.mtime = -1.0
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.error("Configuration reload failed: %s", exc)

    def get_category(self, suffix: str) -> str:
        try:
            current_mtime = self.path.stat().st_mtime
        except OSError:
            current_mtime = -1.0

        with self.lock:
            old_mtime = self.mtime

        if current_mtime != -1.0 and current_mtime != old_mtime:
            self.reload()

        with self.lock:
            for category, extensions in self.data.items():
                if suffix.lower() in extensions:
                    return category
        return "Others"


class HistoryLogger:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def record(self, src: Path, dst: Path, category: str):
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "category": category,
            "from": str(src),
            "to": str(dst),
        }
        with self.lock:
            history = self._read()
            history.append(entry)
            try:
                self.path.write_text(
                    json.dumps(history, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                log.exception("Failed to save move history.")

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            log.exception("Failed to read move history.")
            return []


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.moved = 0
        self.skipped = 0
        self.errors = 0
        self.retried = 0

    def add(self, kind: str):
        with self.lock:
            if kind == "moved":
                self.moved += 1
            elif kind == "skipped":
                self.skipped += 1
            elif kind == "error":
                self.errors += 1
            elif kind == "retried":
                self.retried += 1

    def snapshot(self):
        with self.lock:
            return {
                "moved": self.moved,
                "skipped": self.skipped,
                "errors": self.errors,
                "retried": self.retried,
            }


class OrganizerEngine:
    def __init__(self, watch_dir: Path, ui_queue: queue.Queue):
        self.watch_dir = watch_dir.expanduser().resolve()
        self.ui_queue = ui_queue
        self.config = ConfigManager(CONFIG_PATH)
        self.history = HistoryLogger(HISTORY_PATH)
        self.stats = Stats()
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.observer = None
        self.stopped = threading.Event()

        self.pending_lock = threading.Lock()
        self.pending: set[str] = set()
        self.timer_lock = threading.Lock()
        self.timers: dict[str, threading.Timer] = {}
        self.move_lock = threading.Lock()
        self.retry_lock = threading.Lock()
        self.retry_counts: dict[str, int] = {}

    def notify(self, message: str):
        self.ui_queue.put(("log", message))

    def is_valid_source(self, path: Path) -> bool:
        try:
            return (
                path.is_file()
                and path.resolve().parent == self.watch_dir
                and path.name.lower() not in {n.lower() for n in IGNORED_NAMES}
                and path.suffix.lower() not in TEMP_SUFFIXES
            )
        except OSError:
            return False

    def start(self):
        if self.stopped.is_set():
            raise RuntimeError("This organizer instance has already stopped.")
        if not self.watch_dir.is_dir():
            raise ValueError("The selected folder does not exist.")

        self.observer = Observer()
        self.observer.schedule(
            OrganizerEventHandler(self),
            str(self.watch_dir),
            recursive=False,
        )
        self.observer.start()
        self.notify(f"Monitoring started: {self.watch_dir}")
        self.executor.submit(self.startup_scan)

    def startup_scan(self):
        self.notify("Scanning existing files...")
        try:
            for path in self.watch_dir.iterdir():
                if self.stopped.is_set():
                    break
                if self.is_valid_source(path):
                    self.schedule(path, immediate=True)
            self.notify("Startup scan completed.")
        except OSError as exc:
            self.notify(f"Startup scan failed: {exc}")
            log.exception("Startup scan failed.")

    def schedule(self, path: Path, immediate: bool = False):
        if self.stopped.is_set():
            return
        try:
            path = path.resolve()
        except OSError:
            return
        if not self.is_valid_source(path):
            return

        key = str(path)
        with self.pending_lock:
            if key in self.pending:
                return
            self.pending.add(key)

        if immediate:
            self._submit(path)
            return

        timer = threading.Timer(DEBOUNCE_SECONDS, self._submit, args=(path,))
        timer.daemon = True
        with self.timer_lock:
            previous = self.timers.pop(key, None)
            if previous:
                previous.cancel()
            self.timers[key] = timer
            timer.start()

    def _submit(self, path: Path):
        key = str(path)
        with self.timer_lock:
            self.timers.pop(key, None)

        if self.stopped.is_set():
            self._release(path)
            return

        try:
            self.executor.submit(self._process, path)
        except RuntimeError:
            self._release(path)

    def _release(self, path: Path):
        with self.pending_lock:
            self.pending.discard(str(path))

    def _stable(self, path: Path) -> bool:
        deadline = time.monotonic() + STABILITY_TIMEOUT
        previous_size = None
        stable_checks = 0

        while time.monotonic() < deadline:
            if self.stopped.is_set():
                return False
            try:
                current_size = path.stat().st_size
            except OSError:
                return False

            if current_size == previous_size:
                stable_checks += 1
            else:
                stable_checks = 0

            if stable_checks >= STABILITY_CHECKS:
                return True

            previous_size = current_size
            time.sleep(STABILITY_INTERVAL)
        return False

    def _process(self, path: Path):
        retry_reason = None
        try:
            if self.stopped.is_set() or not self.is_valid_source(path):
                return
            if not self._stable(path):
                retry_reason = "File is still changing or inaccessible."
                return
            if not self.is_valid_source(path):
                return

            category = self.config.get_category(path.suffix)
            category_path = Path(category)
            if (
                category_path.is_absolute()
                or len(category_path.parts) != 1
                or category in {".", ".."}
                or "/" in category
                or "\\" in category
            ):
                raise ValueError(f"Invalid category name in config.json: {category!r}")

            dest_folder = self.watch_dir / category
            dest_folder.mkdir(exist_ok=True)

            with self.move_lock:
                if not self.is_valid_source(path):
                    return
                destination = self._unique_dest(dest_folder, path.name)
                shutil.move(str(path), str(destination))

            with self.retry_lock:
                self.retry_counts.pop(str(path), None)

            self.history.record(path, destination, category)
            self.stats.add("moved")
            self.notify(f"MOVED: {path.name} -> {category}/{destination.name}")

        except (PermissionError, BlockingIOError) as exc:
            retry_reason = f"File is locked or access was denied: {exc}"
        except OSError as exc:
            retry_reason = f"Temporary file error: {exc}"
        except Exception as exc:
            log.exception("Failed to process %s", path)
            self.stats.add("error")
            self.notify(f"ERROR: {path.name}: {exc}")
        finally:
            self._release(path)
            if retry_reason:
                self._retry(path, retry_reason)

    def _retry(self, path: Path, reason: str):
        if self.stopped.is_set() or not self.is_valid_source(path):
            return

        key = str(path)
        with self.retry_lock:
            count = self.retry_counts.get(key, 0) + 1
            self.retry_counts[key] = count

        if count > MAX_RETRIES:
            with self.retry_lock:
                self.retry_counts.pop(key, None)
            self.stats.add("skipped")
            self.notify(f"SKIPPED after retries: {path.name}")
            log.warning("Retry limit reached for %s: %s", path, reason)
            return

        delay = min(RETRY_BASE_SECONDS * (2 ** (count - 1)), 60)
        self.stats.add("retried")
        self.notify(f"RETRY {count}/{MAX_RETRIES}: {path.name} in {delay}s")

        timer = threading.Timer(delay, self._retry_submit, args=(path,))
        timer.daemon = True
        with self.timer_lock:
            old_timer = self.timers.pop(key, None)
            if old_timer:
                old_timer.cancel()
            self.timers[key] = timer
            timer.start()

    def _retry_submit(self, path: Path):
        key = str(path)
        with self.timer_lock:
            self.timers.pop(key, None)

        if self.stopped.is_set() or not self.is_valid_source(path):
            return

        with self.pending_lock:
            if key in self.pending:
                return
            self.pending.add(key)

        self._submit(path)

    def _unique_dest(self, folder: Path, filename: str) -> Path:
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        candidate = folder / filename
        number = 1
        while candidate.exists():
            candidate = folder / f"{stem}_{number}{suffix}"
            number += 1
        return candidate

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()

        with self.timer_lock:
            timers = list(self.timers.values())
            self.timers.clear()
        for timer in timers:
            timer.cancel()

        if self.observer:
            self.observer.stop()
            self.observer.join()

        self.executor.shutdown(wait=True, cancel_futures=True)
        self.notify("Monitoring stopped.")


class OrganizerEventHandler(FileSystemEventHandler):
    def __init__(self, engine: OrganizerEngine):
        super().__init__()
        self.engine = engine

    def on_created(self, event):
        if not event.is_directory:
            self.engine.schedule(Path(event.src_path))

    def on_modified(self, event):
        if not event.is_directory:
            self.engine.schedule(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory:
            self.engine.schedule(Path(event.dest_path))


class OrganizerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Real-Time File Organizer")
        self.root.geometry("900x650")
        self.root.minsize(720, 520)

        self.ui_queue = queue.Queue()
        self.engine: OrganizerEngine | None = None
        self.closing = False

        self.folder_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.status_var = tk.StringVar(value="Stopped")
        self.moved_var = tk.StringVar(value="0")
        self.skipped_var = tk.StringVar(value="0")
        self.errors_var = tk.StringVar(value="0")
        self.retried_var = tk.StringVar(value="0")

        self._build_ui()
        self.root.after(150, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="Real-Time File Organizer",
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            main,
            text="Choose a folder, organize existing files, and monitor new files.",
        ).pack(anchor="w", pady=(0, 16))

        folder_frame = ttk.LabelFrame(main, text="Folder to monitor", padding=10)
        folder_frame.pack(fill="x")
        self.folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var)
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(folder_frame, text="Browse...", command=self._browse).pack(side="left")

        button_frame = ttk.Frame(main)
        button_frame.pack(fill="x", pady=12)
        self.start_button = ttk.Button(
            button_frame, text="Start Monitoring", command=self._start
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(
            button_frame, text="Stop Monitoring", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="left")
        ttk.Label(button_frame, textvariable=self.status_var).pack(side="right")

        stats_frame = ttk.LabelFrame(main, text="Statistics", padding=10)
        stats_frame.pack(fill="x", pady=(0, 12))
        for label, variable in (
            ("Moved", self.moved_var),
            ("Skipped", self.skipped_var),
            ("Errors", self.errors_var),
            ("Retries", self.retried_var),
        ):
            cell = ttk.Frame(stats_frame)
            cell.pack(side="left", expand=True, fill="x")
            ttk.Label(cell, text=label).pack()
            ttk.Label(cell, textvariable=variable, font=("Segoe UI", 18, "bold")).pack()

        activity_frame = ttk.LabelFrame(main, text="Recent Activity", padding=8)
        activity_frame.pack(fill="both", expand=True)
        self.activity = tk.Text(
            activity_frame, height=12, wrap="word", state="disabled",
            font=("Consolas", 10)
        )
        self.activity.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(activity_frame, command=self.activity.yview)
        scrollbar.pack(side="right", fill="y")
        self.activity.configure(yscrollcommand=scrollbar.set)

    def _browse(self):
        folder = filedialog.askdirectory(
            title="Select folder to monitor",
            initialdir=self.folder_var.get(),
        )
        if folder:
            self.folder_var.set(folder)

    def _start(self):
        folder = Path(self.folder_var.get()).expanduser()
        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("Invalid Folder", "Please select an existing folder.")
            return
        if self.engine is not None:
            messagebox.showwarning("Already Running", "Stop the current session first.")
            return

        engine = OrganizerEngine(folder, self.ui_queue)
        try:
            engine.start()
        except Exception as exc:
            log.exception("Could not start organizer.")
            try:
                engine.stop()
            except Exception:
                pass
            messagebox.showerror("Startup Error", f"Could not start monitoring:\n{exc}")
            return

        self.engine = engine
        self.status_var.set("Running")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.folder_entry.configure(state="disabled")

    def _stop(self):
        engine = self.engine
        if engine is None:
            return

        self.engine = None
        self.status_var.set("Stopping...")
        self.stop_button.configure(state="disabled")

        def shutdown():
            try:
                engine.stop()
            except Exception:
                log.exception("Error during shutdown.")
            finally:
                self.ui_queue.put(("stopped", None))

        threading.Thread(target=shutdown, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_activity(payload)
                elif kind == "stopped":
                    self.status_var.set("Stopped")
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.folder_entry.configure(state="normal")
                    if self.closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass

        if self.engine is not None:
            snapshot = self.engine.stats.snapshot()
            self.moved_var.set(str(snapshot["moved"]))
            self.skipped_var.set(str(snapshot["skipped"]))
            self.errors_var.set(str(snapshot["errors"]))
            self.retried_var.set(str(snapshot["retried"]))

        self.root.after(150, self._poll_queue)

    def _append_activity(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity.configure(state="normal")
        self.activity.insert("end", f"[{timestamp}] {message}\n")
        line_count = int(self.activity.index("end-1c").split(".")[0])
        if line_count > 500:
            self.activity.delete("1.0", "101.0")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _close(self):
        if self.engine is None:
            self.root.destroy()
            return
        if not messagebox.askyesno("Exit", "Stop monitoring and exit?"):
            return
        self.closing = True
        self._stop()

    def run(self):
        self.root.mainloop()


def main():
    root = tk.Tk()
    OrganizerGUI(root).run()


if __name__ == "__main__":
    main()
