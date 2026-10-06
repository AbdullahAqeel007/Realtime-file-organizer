Real-Time File Organizer 📁⚡

An automated Python tool that monitors a directory (like your Downloads folder) and instantly organizes newly created or downloaded files into designated subfolders based on their file extensions.

Powered by watchdog, it runs efficiently in the background with near-zero CPU usage and includes safeguards for active browser downloads and filename collisions.

🌟 Key Features

Real-Time Monitoring: Uses OS-native file system events (watchdog) to trigger organization instantly upon file creation or rename events.

Cross-Platform: Works seamlessly across Windows, macOS, and Linux without modifying folder path strings manually.

Configurable Rules: Simple JSON file mapping extensions to target category folders.

Browser-Safe Handling: Automatically ignores active download extensions (.crdownload, .tmp, .part, .download) to avoid corrupting file transfers.

Collision Protection: Prevents accidental overwrites by appending numeric increments (file_1.png) if a file with the same name already exists.

Detailed Console Logging: Tracks file operations and handles permission locks or missing paths gracefully with clean timestamped logs.

📂 Project Structure

file-organizer/
│
├── organizer.py       # Main Python script using Watchdog
├── config.json        # Categorization rules (extensions to folders)
├── requirements.txt   # Dependencies
└── README.md          # Documentation


⚙️ Prerequisites

Before installing, ensure you have:

Python 3.8+ installed (python --version or python3 --version).

Git installed (or download the source directly as a ZIP file).

📥 Installation & Setup (Any Computer)

Follow these steps to set up and run the script on any machine (Windows, macOS, or Linux).

1. Clone or Download the Project

Option A: Using Git

git clone https://github.com/your-username/realtime-file-organizer.git
cd realtime-file-organizer


Option B: Direct Download

Click Code -> Download ZIP on the repository page.

Extract the archive and open your terminal/PowerShell inside the extracted folder.

2. Set Up a Virtual Environment (Recommended)

Isolated environments prevent library conflicts with other Python projects on your machine.

Windows (PowerShell):

python -m venv venv
.\venv\Scripts\activate


macOS / Linux (Terminal):

python3 -m venv venv
source venv/bin/activate


3. Install Dependencies

Install required third-party libraries using the requirements.txt file:

pip install -r requirements.txt


🚀 Running the Organizer

By default, the script targets and monitors your system's default Downloads folder (Path.home() / "Downloads").

Execute the Script:

Windows:

python organizer.py


macOS / Linux:

python3 organizer.py


Terminal Output:

2026-10-06 22:50:00 - INFO - Started real-time monitoring on: /Users/username/Downloads
2026-10-06 22:50:00 - INFO - Press Ctrl+C to stop.


To stop real-time monitoring at any time, press Ctrl + C in your active terminal.

🔧 Customization

Changing Mapped File Categories (config.json)

You can add or remove extensions or create custom folder categories by editing config.json:

{
  "Images": [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"],
  "Documents": [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv"],
  "Audio": [".mp3", ".wav", ".flac"],
  "Video": [".mp4", ".mkv", ".mov"],
  "Archives": [".zip", ".tar", ".gz", ".7z", ".rar"],
  "Code": [".py", ".js", ".html", ".css", ".json"]
}


Files with unlisted extensions will automatically move to an Others folder.

Changing Monitored Directory

To watch a folder other than Downloads (e.g., your Desktop), edit line 100 in organizer.py:

if __name__ == "__main__":
    # Custom directory example:
    WATCH_DIRECTORY = Path.home() / "Desktop"
    start_monitoring(WATCH_DIRECTORY)


📜 License

Distributed under the MIT License. Feel free to modify and use it for your own workflow!