import json
from datetime import datetime

def add_data_to_json(date, mode, filename="mode.json"):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        data = []

    if isinstance(date, datetime):
        date = date.isoformat()
    elif not isinstance(date, str):
        date = str(date)

    if not any(entry['date'] == date for entry in data):
        new_entry = {"date": date, "mode": mode}
        data.append(new_entry)
        data.sort(key=lambda x: datetime.fromisoformat(x['date']))
        
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)