# 伦敦多源数据采集

启动网页、Linux/Windows 部署和配置密钥的步骤见[项目 README](../README.md)与[部署说明](Deploy_CN.md)。本文说明 LAQN、Breathe London、TfL 和英国气象局数据如何采集、存储和更新。

网页默认每 5 分钟检查到期采集并刷新表格，可选择关闭、1、5、15 分钟。点击“更新数据”会启动同一采集流程并显示进度。各来源沿用原有请求频率和额度。网页与定时采集任务共享进程锁，已有采集运行时会等待完成。

数据中心默认展示活动站点在最近 48 小时内的最新值。LAQN 可选 15 分钟浓度、小时浓度或 PM₁₀ 和 O₃ 的小时指数。Breathe London 展示官网地图和曲线中的 PM₂.₅ 和 NO₂ 浓度。TfL 可选自行车与道路事件，气象支持区域筛选。点击站名可查看元数据，并按指标和粒度筛选最近 48 小时的观测。显示时区可切换伦敦、UTC 和北京。每个指标保留自己的观测时间、获取时间、单位和质量状态。

数据中心通过 `/api/measurements` 的可选 `start` 和 `end` 查询这 48 小时。最新值模式为每站、每指标选择窗口内最新的观测，查询遵循 `start <= t < end`。

## 启动

在项目根目录先运行一次采集和状态检查。Linux 使用 `python3` 或 `.venv/bin/python`，Windows 使用 `.\.venv\Scripts\python`：

```bash
python tools/collect_live.py run-once
python tools/collect_live.py status
```

```powershell
.\.venv\Scripts\python tools\collect_live.py run-once
.\.venv\Scripts\python tools\collect_live.py status
```

LAQN、TfL 和 Breathe London 无需密钥。英国气象局密钥可以放在当前用户的环境变量 `MET_OFFICE_API_KEY` 中，也可以放在项目目录外的纯文本文件：

- Windows：`%APPDATA%\LondonAirWatch\credentials.txt`
- Linux：`~/.config/LondonAirWatch/credentials.txt`

格式如下（把示例值换成真实密钥，值不加引号）。旧文件里的 `BREATHE_LONDON_API_KEY` 条目可保留。

```text
MET_OFFICE_API_KEY=你的-Met-Office-密钥
```

采集器优先读取环境变量，缺失时才读取此文件。文本文件是明文，应只让当前用户访问；它位于项目目录外，不会进入项目备份或提交。不要把密钥放入网页、命令行参数、数据库或日志。使用文件方式保存后，无需重新登录；运行 `run-once --force` 可立即检查。使用用户环境变量方式保存后，Windows 需要重新登录，Linux 新开的 shell 会生效。

Linux 服务器用 cron 定时采集：

```cron
*/5 * * * * cd /path/to/London_UrbanFlow && bash scripts/collector.sh >> data/live/collector.log 2>&1
```

若服务器直连 TfL 或 Hugging Face 被拒绝，但本机 HTTP 代理可用，可在 `data/live/outbound_proxy.txt` 中只写一行代理地址，例如 `http://127.0.0.1:17898`。该文件已被 Git 忽略；采集器在没有系统 HTTPS 代理环境变量时读取它，并只接受不含用户名和密码的代理地址。TfL 实时采集与自行车公开归档下载都优先使用 curl，因此网页更新和 cron 启动的采集子进程也能使用此代理。先确认代理服务长期运行，再配置该文件。

Windows 用任务计划程序：

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_live_task.ps1 -Python .\.venv\Scripts\python.exe
```

任务名是 `LondonAirWatchLiveCollector`，每 5 分钟运行一次 `run-once`；程序自己判断哪个来源到期。Windows 任务使用当前用户身份，用户未登录或电脑关机时不会采集。检查任务：`Get-ScheduledTask -TaskName LondonAirWatchLiveCollector`。停止定时采集：`Unregister-ScheduledTask -TaskName LondonAirWatchLiveCollector -Confirm:$false`。安装脚本不会自动创建密钥文件。Linux 用 `crontab -l` 检查、`crontab -e` 修改或删除对应行。

## 数据与时间

数据库默认是 `data/live/airwatch_live.sqlite3`（可用 `URBANFLOW_DB` 覆盖，Web 服务、采集、`diagnose_data.py` 和备份工具统一读取该变量）；压缩原始响应在 `data/live/raw/<来源>/<UTC日期>/`，默认保留 90 天。`tools/backup_db.py` 可在采集运行时生成一致性备份。结构化记录长期保留。站点以 `(source, source_id)` 区分，不因名称相同而合并。数据库采用 UTC ISO 时间，同时保留原始时间文本和 `time_basis`。TfL 停靠站如无源时间，则以获取时间记一条快照并标记 `retrieval_time`；这不代表设备实际观测时间。气象局的 geohash/区域名不等于气象站精确经纬度，因此坐标保持空值。

`measurements` 按指标保存浓度、指数、气象值或自行车数。浓度和指数使用不同 `metric`；缺失或负值不填零。LAQN 小时浓度接口未提供逐值质量标记，因此存 `U`；15 分钟 CSV 则保留 `P`（暂定）或 `R`（已审定）。两种浓度使用同一 `metric`，以 `period_seconds=3600` 或 `900` 区分。`road_events` 保存当前道路事件，`event_versions` 和 `measurement_versions` 保存变更前的版本。`fetch_runs` 留存每次请求的 HTTP 状态、耗时（`duration_ms`）、解析计数、最新观测时间和响应 SHA256；`job_state` 供到期判断与 `status` 命令使用。

## 来源与限制

- LAQN：[接口目录](https://api.erg.ic.ac.uk/AirQuality/help)与[15 分钟 CSV 说明](https://www.londonair.org.uk/london/asp/datadownload.asp)。每日动态发现伦敦组活动站。每小时收录 PM₁₀ 和 O₃ 的最新小时指数，获取最近约 48 小时的小时浓度，并逐站下载四种污染物的 15 分钟 CSV。CSV 首次补取最近 7 天，之后每小时重取最近约 48 小时以接收修订。CSV 的 `ReadingDateTime` 是观测时段开始时间，但无时区标识；经与 LAQN GMT 小时标签及英国夏令时切换日的 96 个时段核对后，按 GMT 解释，并在 `time_basis` 标记该推断。普通质量浓度单位规范为 `ug/m3`；若颗粒物的单位注明 `reference equiv by VCM` 等换算信息，则原样保留。CSV 的 15 分钟粒度不保证 15 分钟发布延迟；各站是否有值以原始文件为准。该来源建议由单一采集端代理和缓存，勿让每个浏览器直接轮询。
- Breathe London：`breathe_web_hourly` 每小时读取[官网地图数据](https://www.breathelondon.org/api/air-quality.json)，更新站点目录和 PM₂.₅、NO₂ 浓度。每个指标使用自己的来源时间。随后 `breathe_web_reconcile` 每次最多为 12 个活动站补查最近 48 小时的[站点曲线](https://www.breathelondon.org/sensors/sensor-profile?sitecode=BL0027)。优先补查尚未检查的站；已检查站在观测超过 3 小时且距上次成功补查至少 1 小时后可再次补查，其他成功站至少间隔 24 小时，单站失败后至少间隔 1 小时。SQLite 保存每站进度。相同观测优先使用曲线记录，保留修订历史，质量状态为 `U`。用 `status` 查看最新观测和 `breathe_web_coverage`，后者统计过去 24 小时成功补查的活动站及最近一次补查失败的活动站。成功补查也可能返回空序列。

旧版写入的 PM₂.₅ 和 NO₂ 指数留存于 SQLite，数据中心查询会滤掉这两项。上游原始响应按现有保留期归档。
- TfL：[统一 API](https://api.tfl.gov.uk/) 与 [API 产品说明](https://api-portal.tfl.gov.uk/products)。匿名访问目前限每分钟 50 次，本项目每 5 分钟仅请求自行车和道路各一次，因此无需密钥。采集器优先使用系统 curl；Windows 若 Internet Settings 配置了不含账号密码的代理，采集器会让 curl 显式复用该代理（curl 本身不会自动读取此设置）。TLS 证书验证保持开启。采集停靠站状态与当前道路扰动；事件不是交通流量。道路接口失败时不会把旧事件误判为已结束。
- 英国气象局：[地面观测](https://datahub.metoffice.gov.uk/docs/o/category/observations/overview)。官方逐小时发布最近 48 小时的观测；项目每小时采集 Greater London 站点，其他已收录站点每日采集，中心与四方锚点每日检查伦敦附近站点。按当前伦敦 3 站计算，逐小时采集约用 72 次/天；API 免费配额为 360 次/天，本地采集器限制为 300 次/天。首次全国采集运行 `python tools/collect_met_all.py`：通过英国范围坐标及每次最多五个邻近 geohash 发现站点，去重后逐站获取最近 48 小时的小时观测；进度保存在 `data/live/metoffice_national_bootstrap.json`，中断后可重跑。九项天气字段按官方样本解析；数值单位按 Met Office Land Observations 产品 Glossary 固定写入（温度 °C、湿度 %、气压 hPa、风速与阵风 m/s、能见度 m），风向、天气代码和气压倾向属于代码值，单位留空。启动服务时会把历史气象记录中缺失的单位补齐一次。

气象局 Land Observations 免费套餐目前允许每天 360 次请求（UTC 日重置）。全国约 150 个站点每天各请求一次，加上 5 次每日伦敦站点查询，通常约 155 次/天；同一响应已包含最近 48 小时，不需要为每个小时单独请求。为给重试和人工检查留余量，常规采集器对自身气象局请求设置每天 300 次的本地上限；首次全国发现脚本的临时上限为每天 350 次，以便在免费额度内完成站点发现和下载。失败的网络尝试也计入，`status` 会显示当天用量。最近站点接口可能返回英国以外地点：这些响应保留在原始归档中，但境外站点不加入常规采集。观测接口返回 404 的站点保留元数据并标为不活动。人工从别的程序使用同一密钥不会出现在本地计数中，仍需留意平台总额度。

`status` 会报告缺密钥、请求错误、任务过期或数据过期。`changes` 是该次写入或修订的记录数，重复采集成功时可以为零。道路事件的 `latest_data` 使用来源修改时间。获得密钥后运行 `run-once --force` 验证实际接口。备份时复制整个 `data/live` 目录，SQLite 处于 WAL 模式，在线复制前应使用 SQLite 的备份命令或先停止任务。

15 分钟任务在 `status` 中名为 `laqn_15min`，另有 `laqn_15min_coverage` 显示最近 48 小时有非空观测的活动站数量。CSV 的空值不会填零，也不会清除此前已保存的有效值；请用最新观测时间判断数据是否仍新鲜。原始 CSV 与 JSON 分别压缩为 `.csv.gz` 和 `.json.gz`，均保存 90 天。可用 SQLite 查询 15 分钟观测，例如：

```sql
SELECT source_id, metric, valid_at_utc, value_num, unit, quality
FROM measurements
WHERE source='laqn' AND period_seconds=900
ORDER BY valid_at_utc DESC LIMIT 20;
```

## Westminster 离线归档（无需 API 密钥）

[Breathe London 官方站点页](https://www.breathelondon.org/sensors/sensor-profile?sitecode=BL0027)提供逐站、逐年和逐月 CSV 下载。[Westminster 市政府](https://www.westminster.gov.uk/about-council/data/air-quality-data/air-quality-data-download/sensor-air-quality-data)还公开包含 Breathe London 的传感器年度 ZIP，覆盖 Westminster 区。离线工具将该 ZIP 导入为 `source=breathe_wcc`，供本地归档查询。网页上的 Breathe London 观测使用 `source=breathe`。

可重新下载并导入当前年度（在项目根目录运行；Windows 使用 `curl.exe` 和 `.\.venv\Scripts\python`）：

```bash
curl -L --retry 3 --retry-all-errors -o data/live/imports/westminster_2026.zip 'https://www.westminster.gov.uk/media/document/wcc-air-quality-sensor-export-2026'
python tools/import_breathe_wcc.py --year 2026
python tools/collect_live.py status
```

其他年份可将命令中的年份及 `--year` 同步改为 2021–2025。导入器只选择原表 `source=Breathe London` 的 PM₂.₅、NO₂小时浓度，保留原始 ZIP 校验值，重复导入可去重，修订会留旧版本。Westminster CSV 的时间没有时区标识；从春季夏令时缺失的 01:00 小时推断为伦敦本地时钟，`time_basis` 明确标记这一推断。秋季回拨时无法唯一对应 UTC 的 01:00 记录被跳过，不会被随意指定为某个小时。该导出没有逐值审定标记，质量状态为 `U`。使用时注明数据来自 [Breathe London](https://www.breathelondon.org/) 和 Westminster 市政府。

首次部署后至少观察 24 小时：运行 `status`，确认 LAQN/TfL 的最近成功时间持续前进；在定时任务（Windows 任务计划程序或 Linux cron）中查看上次结果与下一次运行时间；检查 `fetch_runs` 的失败、429 和耗时，并核对 `measurements` 的唯一键、最新真实观测时间和 `measurement_versions`。有密钥的来源在实际联通验收前会一直显示 `pending_credentials`。


## 地图与联动分析

城市总览采用本地固定版本 MapLibre GL JS 5.6.2 与 OpenFreeMap 浅色 Positron 底图，图表采用 ECharts 5.6.0。伦敦行政边界使用项目提供的 GeoJSON 叠加；河流专题图层已从当前页面移除。自行车使用本地 Supercluster 8.0.1 生成聚合标记：缩放级别小于 13 时以可点击的数字圆圈展示，数字为站点数；放大后显示全部原始站点，可查看车辆与空位。聚合标记通过 DOM 绘制；聚合组件加载失败时直接显示原始点位。更新数据、历史回放和图层开关均同步更新标记。第三方代码及许可证在 `assets/vendor/`。底图需要网络连接；底图资源失败时仍可查询业务数据、图表与表格。

城市总览与污染分析使用最近 48 小时的观测，自行车时空分析使用最近 7 天的库存快照。进入历史回放时固定查询窗口，按所选小时的结束时刻取值，窗口末端以进入回放时的时间为上限。LAQN 小时值优先采用来源小时数据，缺少小时值时至少使用 3 个不同的 15 分钟观测求均值。返回的 `sample_count` 与 `aggregation` 保留计算依据。地图最新状态使用 LAQN 的 15 分钟浓度及 Breathe London 官网观测。地图圆点为 LAQN、菱形为 Breathe London。浓度按窗口内的数据分级，灰色代表缺测。

自行车每次成功采集都会写入 `bike_snapshots`，记录获取时间、来源修改时间、车辆、空位和容量。启动服务时从过去 7 天的自行车及过去 48 小时的道路成功原始响应补回历史快照，恢复进度保存在 `traffic_archive_imports`。道路列表同样保留成功快照，以准确恢复某次列表中的事件范围和状态，并继续保存原有事件版本。城市总览历史回放优先使用游标之前 10 分钟内的本地快照。自行车可以采用 20 分钟内、比本地快照更新的公开归档，并标注来源和采集时间。仍无可用归档时，允许向前沿用 2 小时内的本地快照并标注时间差。超过 2 小时或无快照时，道路事件可按前后两次完整列表的交集重建并标注「重建」。重建只包含中断前后两次完整列表中都在列的事件；只在单侧出现的事件无法确认，保持未知。原始档案缺失的区间不插值、不补零。

道路列表还使用 [london-buses 中的 TIMS 公开快照](https://github.com/farhanmunim/london-buses/commits/main/data/api/roadworks.json)补录最近 7 天。该公开归档通常每天约 2 批完整列表，两次完整列表之间没有可导入的逐小时列表，因此这些小时只能按前后列表交集重建，而不是从已发布的归档中取得精确状态。本地 Windows 定时采集每 5 分钟运行一次，正常运行时游标前 2 小时内都有本地快照，48 小时窗口几乎不需要重建；只有采集器停采的区间才会出现较长的「重建」段。TfL 接口只提供当前扰动列表，不能事后补取过去某个小时的历史列表。

自行车时空分析使用 [london-cycles 公开归档](https://huggingface.co/datasets/feregrino/london-cycles)补录最近 7 天。归档由 feregrino 收集，基于 TfL Open Data，数据许可为 CC BY-SA 3.0。现有采集流程在末尾每小时检查一次，首次部署可运行 `python tools/collect_live.py backfill-bikes`。归档按实际采集时间存储，记录源文件版本和 SHA256。回放优先使用 10 分钟内的本地快照，公开归档最多回看 20 分钟，实际断档显示缺测。实时地图与共享单车路线继续读取本地 TfL 快照。

2026-11-13 09:15–2026-11-16 14:15 UTC，TfL BikePoint 上游先连续 54 次返回同一组库存（8175 辆车、10755 个空位，已冻结），随后所有站点返回 `bikes=0`、`empty_docks=0`，而 `docks` 仍为正常值。公共归档原样记录了这段数据，因此回放会看到全站 0 车、0 空位；这不是本地采集、解析或夜间合并错误，归档在 2026-11-16 14:30 UTC 恢复为真实值。导入器现在把“整批所有站点均无车且无空位”的快照视为无效并跳过，已有无效批次会在归档补录时清理；回放中该时段显示缺测，不插值、不补零。

选择监测站后显示周围 500 米自行车站和 1 公里道路事件，可切换为 500 米、1 公里或 2 公里。自行车站关联 1 公里内最近的有值污染监测点。道路范围距离使用实际几何；区域气象仅采用 `area=London / Greater London` 的有效站点。总览首行的气象卡片对全部有效伦敦站做逐小时平均（当前 3 站），每项只用有值的站；分析页仍按下拉选择单个背景站。气象站只有 geohash 和区域名，没有精确坐标。同 UTC 小时配对至少 24 对且两列均有变化时计算 Pearson 相关系数，作为观察性分析。

分析接口为 `/api/map`、`/api/analysis/series`、`/api/analysis/nearby`、`/api/analysis/weather`。共同参数 `start`、`end`、`cursor` 使用带时区 ISO 时间，`start <= t < end`，回放数据满足 `t <= cursor`。`source` 可选 `laqn`、`breathe`、`breathe_wcc`。`metric` 支持 `PM25`、`NO2`、`PM10`、`O3` 四种浓度指标。附近查询通过 `kind=air / bike / road` 与 `site` 指定对象，或通过 `kind=point` 与 `lon`、`lat` 指定坐标。`radius`、`road_radius` 控制资源统计范围，`local_air` 返回地点实测、估算、最近站点读数或缺测状态。气象接口使用 `weather_site` 指定伦敦站点，`aggregate=mean` 改为返回全部伦敦站的逐小时平均；series 接口使用逗号分隔的 `sites` 对比最多 4 站。

附近查询设置 `include_air_metrics=1` 时，`local_air_by_metric` 按污染物返回四项地点读数。气象查询设置 `context_only=1` 时，返回站点编号 `station`、站名 `station_name` 及时间窗口内最新的一组气象观测 `latest`；同时设置 `aggregate=mean` 时，`station_name` 为「伦敦 N 站平均」，`latest` 中温度、湿度、风速、气压按各项可用站点平均，并带 `samples` 站数与单位。

智慧出行使用 `POST /api/journeys/plan`，请求体含 `[经度,纬度]` 的 `start`、`end`，`mode=shared_bike / walk / personal_bike`，以及 `max_walk_m=1000 / 2000`。服务器只读取最新 TfL BikePoint 快照，超过 15 分钟时不推荐取还车站。取车站需要至少 1 辆车，还车站需要至少 1 个空位。道路路线由 openrouteservice 的步行与骑行服务计算，需在启动服务的进程中设置 `ORS_API_KEY`。密钥保存在服务端。

响应的 `context.route_air` 按实际道路折线返回有序分块与污染物汇总。服务以每个分块的中点估计浓度，每块通常不超过 250 米，长路线最多取 120 块。LAQN 和 Breathe London 分别使用近三小时的监测数据计算，覆盖率按有估计结果的分块长度占全程距离的比例计算。沿途估计、监测依据和 Defra 的 Greater London DAQI 地区等级可在「出行环境参考」查看。路线服务失败时，该页签仍显示 DAQI 状态。
