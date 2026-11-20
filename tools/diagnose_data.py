#!/usr/bin/env python3
"""Read-only UrbanFlow collection diagnosis. Does not mutate project or credentials."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'live' / 'airwatch_live.sqlite3'
NOW = datetime.now(timezone.utc)
CUTOFF = (NOW - timedelta(hours=48)).isoformat(timespec='seconds')


def section(title):
    print('\n=== ' + title + ' ===')


def rows(db, query, args=()):
    return db.execute(query, args).fetchall()


def table_exists(db, name):
    return bool(rows(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def http_check(name, url):
    curl = shutil.which('curl.exe') or shutil.which('curl')
    if not curl:
        print(f'{name}: curl.exe 未找到（跳过联网检测）')
        return None
    with tempfile.TemporaryDirectory(prefix='urbanflow_diag_') as folder:
        target = str(Path(folder) / 'response.bin')
        try:
            p = subprocess.run([curl, '-L', '--silent', '--show-error', '--connect-timeout', '8',
                                '--max-time', '15', '-A', 'LondonAirWatchDiagnostic/1.0',
                                '-H', 'Accept: application/json', '-o', target,
                                '-w', '%{http_code}', url], capture_output=True, text=True, timeout=22)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'{name}: 无法运行curl: {type(exc).__name__}')
            return None
        code = p.stdout.strip()[-3:]
        path = Path(target)
        size = path.stat().st_size if path.is_file() else 0
        if p.returncode:
            print(f'{name}: 连接失败 curl退出码={p.returncode}; HTTP={code}; {p.stderr.strip()[:180]}')
            return False
        if code != '200':
            print(f'{name}: HTTP {code}; 大小={size}字节（非成功响应）')
            return False
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (UnicodeError, OSError, ValueError):
            print(f'{name}: HTTP 200但响应不是JSON；可能是网站跳转、验证页或接口格式变化')
            return False
        root_keys = ', '.join(list(data)[:4]) if isinstance(data, dict) else 'array'
        print(f'{name}: HTTP 200, JSON有效，大小={size}字节，顶层={root_keys}')
        return True


def main():
    section('运行环境')
    print('Python:', sys.version.split()[0], sys.executable)
    print('项目目录:', ROOT)
    print('数据库:', DB)
    if not DB.exists():
        print('数据库文件不存在：当前项目尚未生成采集数据库；请先启动服务并执行首次采集。')
        return
    print('数据库大小:', DB.stat().st_size, '字节')
    db = sqlite3.connect(f'{DB.as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        section('数据库记录数')
        if table_exists(db, 'locations'):
            for row in rows(db, 'SELECT source, COUNT(*) AS n, SUM(active) AS active FROM locations GROUP BY source ORDER BY source'):
                print(f"locations {row['source']}: {row['n']}站; active={row['active']}")
            if not rows(db, 'SELECT 1 FROM locations LIMIT 1'):
                print('locations总数为0：地图0/0的直接原因是采集站点目录为空。')
        else:
            print('没有locations表。')
        if table_exists(db, 'measurements'):
            for row in rows(db, 'SELECT source, COUNT(*) AS n, MAX(valid_at_utc) AS latest FROM measurements GROUP BY source ORDER BY source'):
                print(f"measurements {row['source']}: {row['n']}条; 最新观测={row['latest']}")
            if not rows(db, 'SELECT 1 FROM measurements LIMIT 1'):
                print('measurements总数为0：暂无污染物数值入库。')
            section('近48小时有值空气质量（不同粒度）')
            for source in ('laqn', 'breathe'):
                for period in (900, 3600):
                    row = db.execute("""SELECT COUNT(*) AS n, COUNT(DISTINCT source_id) AS stations
                          FROM measurements WHERE source=? AND metric='PM25_CONCENTRATION'
                          AND period_seconds=? AND valid_at_utc>=? AND value_num IS NOT NULL""",
                          (source, period, CUTOFF)).fetchone()
                    print(f"{source} PM2.5 {period//60}分钟: {row['n']}条，{row['stations']}个有值站点")
        if table_exists(db, 'bike_snapshots'):
            print('bike_snapshots:', db.execute('SELECT COUNT(*) FROM bike_snapshots').fetchone()[0])
        section('各采集任务状态及错误信息')
        if table_exists(db, 'job_state'):
            found = rows(db, """SELECT job,status,record_count,last_attempt_utc,
                              latest_observation_utc,error FROM job_state ORDER BY job""")
            if not found: print('job_state为空：采集程序可能尚未真正运行。')
            for r in found:
                print(f"[{r['job']}] {r['status']}; 变更={r['record_count']}; 上次尝试={r['last_attempt_utc']}; 最新数据={r['latest_observation_utc']}")
                if r['error']: print('   错误:', str(r['error'])[:250])
        if table_exists(db, 'fetch_runs'):
            section('最近10条请求异常')
            for r in rows(db, """SELECT job,status,http_status,error,ended_at_utc FROM fetch_runs
                                 WHERE status!='success' ORDER BY id DESC LIMIT 10"""):
                print(f"{r['job']}: {r['status']} HTTP={r['http_status']} {r['error'] or ''} @ {r['ended_at_utc']}")
    finally:
        db.close()
    section('LAQN官方接口联网测试（只读）')
    http_check('LAQN站点目录', 'https://api.erg.ic.ac.uk/AirQuality/Information/MonitoringSites/GroupName=London/Json')
    http_check('LAQN最新小时指数', 'https://api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json')
    print('\n诊断完成。请仅发送以上状态和错误信息；不要发送API密钥。')

if __name__=='__main__':
    try:
        main()
    except (OSError, sqlite3.Error, ValueError) as exc:
        print('诊断脚本出错:', type(exc).__name__, str(exc)[:300])
        raise SystemExit(1)
