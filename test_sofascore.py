import requests
import json
import datetime

url = f"https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{(datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')}"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/"
}
r = requests.get(url, headers=headers)
print("Status Code:", r.status_code)
data = r.json()

if 'events' in data and data['events']:
    print("Found events:", len(data['events']))
    event = data['events'][0]
    print(json.dumps(event, indent=2))
else:
    print("No events found")
