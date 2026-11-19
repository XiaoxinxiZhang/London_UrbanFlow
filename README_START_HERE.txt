london UrbanFlow - Environment and Mobility Observatory
==================================
1. Double-click 02_OPEN_DASHBOARD.bat.
2. Keep the local server window open.
3. The browser opens index.html at http://127.0.0.1:8765.
   If the port is occupied, use the URL printed in the server window.

The page offers City Overview, Smart Journey, Environment and Traffic
Analysis, and Data Center modules for LAQN, Breathe London, TfL and
Met Office records from
data/live/airwatch_live.sqlite3. Click a station for metadata and history.
The database is local runtime data and is not stored in Git. On a fresh
checkout, collect data before expecting populated live charts and tables.
Use Update Data to run due collection jobs and refresh the tables.
Automatic updates default to every five minutes and pause in hidden tabs.

Smart Journey uses current Santander Cycles dock snapshots to find a
pickup station with bikes and a return station with empty docks. It
shows walking, cycling, and final walking directions when a routing
key is configured. Before launching the server, set ORS_API_KEY in
the server process environment to an openrouteservice API key. The key
is sent only by the local Python server, never to the web page. Without
the key, dock availability and local air-quality context remain visible,
but road directions are unavailable. The official Greater London DAQI
feed is used for regional health guidance and requires Internet access.

The layout is designed for a 1920 x 1080 browser viewport.

Python 3.10+ is required. The launcher checks AIRWATCH_PYTHON, Python
Launcher, per-user Python installations, then PATH.
For collection, credentials and data limitations, see docs/Live_Collection_CN.md.
