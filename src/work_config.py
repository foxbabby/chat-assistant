"""Machine-local work integrations, deliberately excluded from source control."""
import json
from config import DATA_DIR


def load_work_settings():
    try:
        data = json.loads((DATA_DIR / 'work-settings.json').read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


WORK_SETTINGS = load_work_settings()
