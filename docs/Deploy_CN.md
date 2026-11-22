# Linux / Windows 部署说明

本项目使用同一个 Python 入口 `run.py` 在 Windows 和 Linux 上启动 Flask 服务。服务器上推荐用 tmux 保持进程；Windows 上直接运行 `run.py` 即可。

## 1. 环境要求

- Python 3.10 或更高版本
- 能访问 PyPI，用于安装 [requirements.txt](../requirements.txt)
- 可选：`tmux`（Linux 后台运行）
- 可选：`node`（运行 JavaScript 测试）
- 网页底图、实时数据和路线服务需要外网连接

## 2. Linux 服务器部署

### 2.1 获取代码并安装依赖

```bash
git clone https://github.com/XiaoxinxiZhang/London_UrbanFlow.git
cd London_UrbanFlow
bash scripts/setup.sh
```

脚本会创建 `.venv`，升级 pip，并安装 Flask 和 waitress。Debian/Ubuntu 若提示缺少 venv，先执行：

```bash
sudo apt update
sudo apt install -y python3-venv
```

### 2.2 前台启动

```bash
bash scripts/serve.sh --host 0.0.0.0 --port 8765
```

默认只监听 `127.0.0.1:8765`；`--host 0.0.0.0` 允许从其他机器访问。启动后终端会打印访问地址和数据库路径。

### 2.3 tmux 后台运行

```bash
bash scripts/tmux.sh start
```

默认行为：

- tmux 会话名：`urbanflow`
- 监听地址：`0.0.0.0:8765`
- 日志：`data/live/server.log`
- 启动后打印 `http://<服务器IP>:8765/`

常用命令：

```bash
bash scripts/tmux.sh status
bash scripts/tmux.sh logs
bash scripts/tmux.sh attach
bash scripts/tmux.sh restart
bash scripts/tmux.sh stop
```

在 tmux 会话中按 `Ctrl+B` 再按 `D` 可以脱离会话，服务继续运行。`attach` 后再按 `Ctrl+C` 会停止服务。

### 2.4 自定义端口和地址

```bash
URBANFLOW_PORT=9000 bash scripts/tmux.sh start
URBANFLOW_HOST=127.0.0.1 URBANFLOW_PORT=9000 bash scripts/tmux.sh start
```

也可以直接传给 `run.py`：

```bash
.venv/bin/python run.py --host 0.0.0.0 --port 9000
```

### 2.5 防火墙

如果使用 `ufw`，只向可信网段开放端口：

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp
```

不要直接把 8765 暴露到公网；本服务没有用户登录和权限控制。

### 2.6 SSH 隧道（更安全的远程访问）

服务器只监听回环地址：

```bash
URBANFLOW_HOST=127.0.0.1 URBANFLOW_PORT=8765 bash scripts/tmux.sh start
```

本地电脑执行：

```bash
ssh -L 8765:127.0.0.1:8765 用户名@服务器地址
```

然后在本机浏览器打开 `http://127.0.0.1:8765/`。这样不需要开放服务器端口。

### 2.7 反向代理与 HTTPS

如果使用 Nginx/Caddy 等反向代理，建议：

- 让 Flask 只监听 `127.0.0.1:8765`
- 由反向代理处理 TLS 和认证
- 设置 `URBANFLOW_TRUSTED_ORIGINS` 为浏览器实际访问的源，例如：

```bash
URBANFLOW_TRUSTED_ORIGINS=https://urbanflow.example.com bash scripts/tmux.sh start
```

如果代理改写了 `Host`，同时设置 `URBANFLOW_TRUSTED_HOSTS`。当前服务不自动信任 `X-Forwarded-*` 头；代理应保留原始 Host，或通过上述变量显式放行。

### 2.8 更新代码

```bash
git pull
bash scripts/setup.sh
bash scripts/tmux.sh restart
```

`data/live/` 和 `.venv/` 不会进入 Git，更新不会覆盖本地数据库。

## 3. Windows 部署

### 3.1 创建虚拟环境并启动

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python run.py --open
```

如果没有 `py`，把 `py -3` 换成 `python`。`--open` 会自动打开浏览器。

### 3.2 允许局域网访问

```powershell
.\.venv\Scripts\python run.py --host 0.0.0.0 --port 8765
```

在 Windows 防火墙中放行对应端口，或只允许专用网络。局域网其他设备访问 `http://<本机IP>:8765/`。

### 3.3 不使用虚拟环境

如果已经全局安装 Flask 和 waitress，也可以直接：

```powershell
python run.py --open
```

## 4. 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `URBANFLOW_HOST` | `127.0.0.1` | 监听地址；服务器用 `0.0.0.0` |
| `URBANFLOW_PORT` | `8765` | 监听端口 |
| `URBANFLOW_DB` | `data/live/airwatch_live.sqlite3` | SQLite 数据库路径 |
| `URBANFLOW_TRUSTED_HOSTS` | 空 | 逗号分隔的允许 Host；为空时不限制 |
| `URBANFLOW_TRUSTED_ORIGINS` | 空 | 逗号分隔的额外允许 POST Origin |
| `URBANFLOW_PYTHON` | 自动查找 | `scripts/*.sh` 使用的 Python 解释器 |
| `URBANFLOW_TMUX_SESSION` | `urbanflow` | tmux 会话名 |
| `URBANFLOW_LOG` | `data/live/server.log` | tmux 启动时的日志路径 |

`run.py` 也支持同名参数：`--host`、`--port`、`--db`、`--open`、`--trusted-host`、`--trusted-origin`。

`scripts/tmux.sh` 会把 `URBANFLOW_HOST/PORT/DB/TRUSTED_HOSTS/TRUSTED_ORIGINS/PYTHON` 传入 tmux 会话。API 密钥不写入 tmux 命令，建议放在凭据文件或登录 shell 的环境变量中；tmux 服务端会继承首次启动时的环境，修改密钥后需要重启 tmux 服务端，或在新会话中手动 `export` 后再启动服务。

### 4.1 API 密钥

项目有两个需要自行申请的密钥。LAQN、Breathe London 和 TfL 采集不需要密钥。

| 变量 | 用途 | 获取方式 |
| --- | --- | --- |
| `ORS_API_KEY` | 智慧出行的步行和骑行道路路线 | 在 [HeiGIT 账户面板](https://account.heigit.org/) 复制 API Key。 |
| `MET_OFFICE_API_KEY` | Met Office 地面气象采集 | 在 [Weather DataHub](https://datahub.metoffice.gov.uk/docs/getting-started) 订阅 Land Observations，再从 My Subscriptions 复制 API Key。 |

Linux：

```bash
export ORS_API_KEY='你的密钥'
export MET_OFFICE_API_KEY='你的密钥'
```

Windows（设置当前用户环境变量，重新打开终端后生效）：

```powershell
[Environment]::SetEnvironmentVariable('ORS_API_KEY','你的密钥','User')
[Environment]::SetEnvironmentVariable('MET_OFFICE_API_KEY','你的密钥','User')
```

Met Office 也可以使用项目目录外的凭据文件：

- Windows：`%APPDATA%\LondonAirWatch\credentials.txt`
- Linux：`~/.config/LondonAirWatch/credentials.txt`

```text
MET_OFFICE_API_KEY=你的-Met-Office-密钥
```

密钥必须在启动 Flask 服务的那个进程中可见，采集子进程会继承该环境。不要把密钥写入网页、数据库、命令行参数、日志或 Git。

## 5. 定时采集

网页打开时会检查到期任务，也可以手动点击“更新数据”。服务器长期运行时，建议再加一个定时任务，保证页面关闭时数据也会更新。

### 5.1 Linux cron

```cron
*/5 * * * * cd /path/to/London_UrbanFlow && bash scripts/collector.sh >> data/live/collector.log 2>&1
```

采集器自身有进程锁，和网页里的“更新数据”不会同时写同一个数据库。各来源按自己的频率判断是否到期，不需要每次运行都请求所有接口。

### 5.2 Windows 任务计划程序

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_live_task.ps1 -Python .\.venv\Scripts\python.exe
```

也可以手动运行：

```powershell
.\.venv\Scripts\python tools\collect_live.py run-once
```

### 5.3 首次采集与诊断

```bash
python tools/collect_live.py status
python tools/collect_live.py run-once
python tools/diagnose_data.py
```

首次启动时 `data/live/` 可能不存在，Flask 会自动创建空数据库和表结构。打开页面后点击“更新数据”即可开始采集，或在终端运行 `run-once`。

## 6. 常见问题

### 端口被占用

服务会明确报错并退出，不会悄悄换端口。换一个端口：

```bash
bash scripts/serve.sh --port 8766
```

### 缺少 Flask 或 waitress

```bash
python -m pip install -r requirements.txt
```

Linux 使用虚拟环境时：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### 缺少 tmux

```bash
sudo apt install tmux
```

### 启动后页面能打开，但“更新数据”返回 403

通常是反向代理改写了 Host 或 Scheme。设置 `URBANFLOW_TRUSTED_ORIGINS` 为浏览器地址栏中的源（包含 `https://` 或 `http://`），必要时再设置 `URBANFLOW_TRUSTED_HOSTS`。

### Windows 上 `bash` 脚本出现 `\r` 错误

仓库通过 [.gitattributes](../.gitattributes) 固定 `.sh` 为 LF。如果脚本是从别处复制到 Linux 的，执行：

```bash
sed -i 's/\r$//' scripts/*.sh
```

### 数据库太大或需要备份

停止服务后复制整个 `data/live/`。SQLite 使用 WAL 模式，在线备份建议使用 SQLite 的备份命令，或先停止采集和服务。

## 7. 安全清单

- 默认只监听 `127.0.0.1`。
- 对外访问优先使用 SSH 隧道或带认证的反向代理。
- 必须开放端口时，用防火墙限制来源网段。
- 不要把 `ORS_API_KEY`、`MET_OFFICE_API_KEY` 写入网页、数据库、日志或命令行参数。
- 不要把 `data/live/airwatch_live.sqlite3` 放到可被静态服务器直接下载的目录；当前 Flask 服务只按白名单提供网页文件。
