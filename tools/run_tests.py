#!/usr/bin/env python3
import pathlib,subprocess,sys,shutil
root=pathlib.Path(__file__).resolve().parents[1]
try:
    import flask
except ImportError:
    sys.exit('缺少 Flask。请先运行: python -m pip install -r requirements.txt')
a=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py','-v'],cwd=root)
if a.returncode:sys.exit(a.returncode)
node=shutil.which('node')
if node:
    for test in ('tests/test_live_time.js','tests/test_insights.js','tests/test_borough_coverage_frontend.js','tests/test_city_frontend.js',
                 'tests/test_bike_analysis_frontend.js','tests/test_city_boundaries.js','tests/test_city_gis.js',
                 'tests/test_live_boundaries.js','tests/test_journey_frontend.js'):
        result=subprocess.run([node,test],cwd=root)
        if result.returncode:sys.exit(result.returncode)
    sys.exit(0)
print('Python tests passed. Install Node.js to run the JavaScript tests.')
