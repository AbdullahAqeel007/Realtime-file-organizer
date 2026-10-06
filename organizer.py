import json
import logging
import time
from pathlib import Path
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        logging.error(f"Configuration file missing: {config_path}")
        return {}
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)

def get_category(file_extension: str, categories: dict) -> str:
    file_ext = file_extension.lower()
    for category, extensions in categories.items():
        if file_ext in extensions:
            return category
    return "Others"

def process_file(file_path: Path, categories: dict) -> None:
    """Organizes a single file into its respective directory."""
    # Skip temporary/partially downloaded files
    if file_path.suffix.lower() in [".tmp", ".crdownload", ".part", ".download"]:
        return

    # Skip if it's a directory or script files
    if file_path.is_dir() or file_path.name in ["organizer.py", "config.json"]:
        return

    # Small pause to allow the operating system to finish writing the file
    time.sleep(1)

    category = get_category(file_path.suffix, categories)
    target_dir = file_path.parent
    dest_folder = target_dir / category
    dest_folder.mkdir(exist_ok=True)

    dest_path = dest_folder / file_path.name

    # Handle duplicate file names
    counter = 1
    while dest_path.exists():
        dest_path = dest_folder / f"{file_path.stem}_{counter}{file_path.suffix}"
        counter += 1

    try:
        shutil.move(str(file_path), str(dest_path))
        logging.info(f"Moved: {file_path.name} -> {category}/{dest_path.name}")
    except PermissionError:
        # File might still be locked by the browser or another process
        logging.warning(f"File locked, skipping for now: {file_path.name}")
    except Exception as e:
        logging.error(f"Error moving {file_path.name}: {e}")

class FileOrganizerHandler(FileSystemEventHandler):
    """Event handler that triggers when new files are created or moved into the folder."""
    def __init__(self, target_dir: Path):
        self.target_dir = target_dir
        self.categories = load_config(CONFIG_PATH)

    def on_created(self, event):
        if not event.is_directory:
            process_file(Path(event.src_path), self.categories)

    def on_moved(self, event):
        # Browsers often create temporary files and rename them when finished
        if not event.is_directory:
            process_file(Path(event.dest_path), self.categories)

def start_monitoring(target_dir: Path) -> None:
    if not target_dir.exists() or not target_dir.is_dir():
        logging.error(f"Invalid directory: {target_dir}")
        return

    event_handler = FileOrganizerHandler(target_dir)
    observer = Observer()
    observer.schedule(event_handler, str(target_dir), recursive=False)

    observer.start()
    logging.info(f"Started real-time monitoring on: {target_dir}")
    logging.info("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logging.info("Stopping watcher...")
    
    observer.join()

if __name__ == "__main__":
    # Specify folder to monitor (e.g., Downloads)
    WATCH_DIRECTORY = Path.home() / "Downloads"
    start_monitoring(WATCH_DIRECTORY)