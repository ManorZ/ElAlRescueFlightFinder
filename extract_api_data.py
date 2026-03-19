"""Extract the real API response from the Playwright tool result file and load into DB."""
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOOL_RESULT_FILE = r"C:\Users\manorz\.claude\projects\C--Users-manorz-ElAlRescueFlightFinder\72850849-bff6-4c38-af9d-8458affd0aa5\tool-results\mcp-plugin_playwright_playwright-browser_evaluate-1773928053734.txt"

with open(TOOL_RESULT_FILE, "r", encoding="utf-8") as f:
    wrapper = json.load(f)

# The wrapper is [{type: "text", text: "### Result\n...json string...\n### Ran..."}]
raw_text = wrapper[0]["text"]

# The actual JSON is between "### Result\n" and "\n### Ran"
result_match = re.search(r"### Result\n(.+?)\n### Ran", raw_text, re.DOTALL)
if result_match:
    json_str = result_match.group(1).strip()
    data = json.loads(json_str)
    # If data is still a string (double-encoded), parse again
    if isinstance(data, str):
        data = json.loads(data)
else:
    # Try direct parse
    data = json.loads(raw_text)
    if isinstance(data, str):
        data = json.loads(data)

# Save raw JSON for reference
os.makedirs("data", exist_ok=True)
with open("data/real_api_response.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print(f"API response saved: {len(data.get('flightsToIsrael', []))} origins")

# Now load into database
from database import init_db
from crawler.seat_availability import load_from_json

init_db()
total, new_count = load_from_json(data)
print(f"Loaded into DB: {total} total flights, {new_count} new")

# Also load destinations from the flight data
from database import get_connection
conn = get_connection()

for route in data.get("flightsToIsrael", []):
    for flight in route.get("flights", []):
        od = flight.get("originDetails", {})
        code = flight.get("routeFrom", "")
        if code:
            conn.execute(
                """INSERT OR REPLACE INTO destinations
                (code, city_name, country_name, continent, is_operational, is_recovery_flight_origin, last_updated)
                VALUES (?, ?, ?, ?, 1, 1, datetime('now'))""",
                (code, od.get("cityName", ""), od.get("countryName", ""), od.get("continentName", ""))
            )
conn.commit()
print("Destinations updated")

# Add a crawl log entry
conn.execute(
    """INSERT INTO crawl_log (started_at, completed_at, flights_found, new_flights, status)
    VALUES (datetime('now'), datetime('now'), ?, ?, 'success')""",
    (total, new_count)
)
conn.commit()
print("Done!")
