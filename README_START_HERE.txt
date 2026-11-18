London AirWatch - Environment and Mobility Observatory
==================================
1. Double-click 02_OPEN_DASHBOARD.bat.
2. Keep the local server window open.
3. The browser opens index.html at http://127.0.0.1:8765.
   If the port is occupied, use the URL printed in the server window.

The page offers a city map, linked analysis charts and data tables for
LAQN, Breathe London, TfL and Met Office records from
data/live/airwatch_live.sqlite3. Click a station for metadata and history.
The database is local runtime data and is not stored in Git. On a fresh
checkout, collect data before expecting populated live charts and tables.
Use Update Data to run due collection jobs and refresh the tables.
Automatic updates default to every five minutes and pause in hidden tabs.

Python 3.10+ is required. The launcher checks AIRWATCH_PYTHON, Python
Launcher, per-user Python installations, then PATH.
For collection, credentials and data limitations, see docs/Live_Collection_CN.md.
