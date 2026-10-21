# Real-Time File Organizer

A Python desktop application that organizes files into extension-based folders. It includes a folder picker, startup scan, retry handling for temporarily inaccessible files, live activity, and move history.

## Requirements

- Python 3.10 or newer recommended
- `watchdog`
- Tkinter (usually included with Python on Windows)

## Setup

1. Extract the ZIP file.
2. Open the extracted `RealTimeFileOrganizer` folder in VS Code.
3. Open the terminal in that folder.
4. Install the dependency:

   ```bash
   python -m pip install -r requirements.txt
   ```

5. Run the application:

   ```bash
   python organizer.py
   ```

6. Select a test folder with **Browse...**, then click **Start Monitoring**.

## Files

- `organizer.py` — application and GUI
- `config.json` — extension-to-category mapping
- `requirements.txt` — Python dependencies
- `move_history.json` — created automatically after successful moves

## Safety notes

- Test with a disposable folder before using your real Downloads folder.
- Files are moved, not copied.
- Files with extensions not listed in `config.json` go into `Others`.
- Temporary download extensions such as `.crdownload` and `.part` are skipped.
- The app watches the selected folder non-recursively; category folders are created inside it.
- Retry handling is best-effort. Some files may remain skipped if they stay locked or keep changing.
