# 伦敦多源数据采集

启动网页和配置密钥的步骤见[项目 README](../README.md)。本文说明 LAQN、Breathe London、TfL 和英国气象局数据如何采集、存储和更新。

网页默认每 5 分钟检查到期采集并刷新表格，可选择关闭、1、5、15 分钟。点击“更新数据”会启动同一采集流程并显示进度。各来源沿用原有请求频率和额度。网页与 Windows 任务共享进程锁，已有采集运行时会等待完成。

数据中心默认展示活动站点在最近 48 小时内的最新值。LAQN 可选 15 分钟浓度、小时浓度或 PM₁₀ 和 O₃ 的小时指数。Breathe London 展示官网地图和曲线中的 PM₂.₅ 和 NO₂ 浓度。TfL 可选自行车与道路事件，气象支持区域筛选。点击站名可查看元数据，并按指标和粒度筛选最近 48 小时的观测。显示时区可切换伦敦、UTC 和北京。每个指标保留自己的观测时间、获取时间、单位和质量状态。

数据中心通过 `/api/measurements` 的可选 `start` 和 `end` 查询这 48 小时。最新值模式为每站、每指标选择窗口内最新的观测，查询遵循 `start <= t < end`。

## 启动

在项目根目录打开 PowerShell，先运行一次：

```powershell
& 'C:\path\to\python.exe' tools\collect_live.py run-once
& 'C:\path\to\python.exe' tools\collect_live.py status
```

LAQN、TfL 和 Breathe London 无需密钥。英国气象局密钥可以放在运行任务的 Windows 用户环境变量 `MET_OFFICE_API_KEY` 中，也可以放在当前用户的纯文本文件 `%APPDATA%\LondonAirWatch\credentials.txt` 中，格式如下（把示例值换成真实密钥，值不加引号）。旧文件里的 `BREATHE_LONDON_API_KEY` 条目可保留。

```text
MET_OFFICE_API_KEY=你的-Met-Office-密钥
```

采集器优先读取环境变量，缺失时才读取此文件。文本文件是明文，应只让当前 Windows 用户访问；它位于项目目录外，不会进入项目备份或提交。不要把密钥放入 `config.js`、网页、命令行参数、数据库或日志。使用文件方式保存后，无需重新登录；运行 `run-once --force` 可立即检查。使用用户环境变量方式保存后，需要重新登录 Windows。安装任务：

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_live_task.ps1 -Python 'C:\path\to\python.exe'
```

任务名是 `LondonAirWatchLiveCollector`，每 5 分钟运行一次 `run-once`；程序自己判断哪个来源到期。任务使用当前用户身份，用户未登录或电脑关机时不会采集。检查任务：`Get-ScheduledTask -TaskName LondonAirWatchLiveCollector`。停止定时采集：`Unregister-ScheduledTask -TaskName LondonAirWatchLiveCollector -Confirm:$false`。安装脚本不会自动创建密钥文件。

## 数据与时间

数据库是 `data/live/airwatch_live.sqlite3`；压缩原始响应在 `data/live/raw/<来源>/<UTC日期>/`，默认保留 90 天。结构化记录长期保留。站点以 `(source, source_id)` 区分，不因名称相同而合并。数据库采用 UTC ISO 时间，同时保留原始时间文本和 `time_basis`。TfL 停靠站如无源时间，则以获取时间记一条快照并标记 `retrieval_time`；这不代表设备实际观测时间。气象局的 geohash/区域名不等于气象站精确经纬度，因此坐标保持空值。

`measurements` 按指标保存浓度、指数、气象值或自行车数。浓度和指数使用不同 `metric`；缺失或负值不填零。LAQN 小时浓度接口未提供逐值质量标记，因此存 `U`；15 分钟 CSV 则保留 `P`（暂定）或 `R`（已审定）。两种浓度使用同一 `metric`，以 `period_seconds=3600` 或 `900` 区分。`road_events` 保存当前道路事件，`event_versions` 和 `measurement_versions` 保存变更前的版本。`fetch_runs` 留存每次请求的 HTTP 状态、耗时（`duration_ms`）、解析计数、最新观测时间和响应 SHA256；`job_state` 供到期判断与 `status` 命令使用。

## 来源与限制

- LAQN：[接口目录](https://api.erg.ic.ac.uk/AirQuality/help)与[15 分钟 CSV 说明](https://www.londonair.org.uk/london/asp/datadownload.asp)。每日动态发现伦敦组活动站。每小时收录 PM₁₀ 和 O₃ 的最新小时指数，获取最近约 48 小时的小时浓度，并逐站下载四种污染物的 15 分钟 CSV。CSV 首次补取最近 7 天，之后每小时重取最近约 48 小时以接收修订。CSV 的 `ReadingDateTime` 是观测时段开始时间，但无时区标识；经与 LAQN GMT 小时标签及英国夏令时切换日的 96 个时段核对后，按 GMT 解释，并在 `time_basis` 标记该推断。普通质量浓度单位规范为 `ug/m3`；若颗粒物的单位注明 `reference equiv by VCM` 等换算信息，则原样保留。CSV 的 15 分钟粒度不保证 15 分钟发布延迟；各站是否有值以原始文件为准。该来源建议由单一采集端代理和缓存，勿让每个浏览器直接轮询。
- Breathe London：`breathe_web_hourly` 每小时读取[官网地图数据](https://www.breathelondon.org/api/air-quality.json)，更新站点目录和 PM₂.₅、NO₂ 浓度。每个指标使用自己的来源时间。随后 `breathe_web_reconcile` 每次最多为 4 个活动站补查最近 48 小时的[站点曲线](https://www.breathelondon.org/sensors/sensor-profile?sitecode=BL0027)。补查成功后间隔至少 24 小时，单站失败后间隔至少 1 小时。SQLite 保存每站进度。相同观测优先使用曲线记录，保留修订历史，质量状态为 `U`。用 `status` 查看最新观测和 `breathe_web_coverage`，后者统计过去 24 小时成功补查的活动站及最近一次补查失败的活动站。成功补查也可能返回空序列。

旧版写入的 PM₂.₅ 和 NO₂ 指数留存于 SQLite，数据中心查询会滤掉这两项。上游原始响应按现有保留期归档。
- TfL：[统一 API](https://api.tfl.gov.uk/) 与 [API 产品说明](https://api-portal.tfl.gov.uk/products)。匿名访问目前限每分钟 50 次，本项目每 5 分钟仅请求自行车和道路各一次，因此无需密钥。Windows 上优先使用系统 `curl.exe`；若 Internet Settings 配置了不含账号密码的代理，采集器会让 curl 显式复用该代理（curl 本身不会自动读取此设置）。TLS 证书验证保持开启。采集停靠站状态与当前道路扰动；事件不是交通流量。道路接口失败时不会把旧事件误判为已结束。
- 英国气象局：[地面观测](https://datahub.metoffice.gov.uk/docs/o/category/observations/overview)。首次全国采集运行 `python tools/collect_met_all.py`：通过英国范围坐标及每次最多五个邻近 geohash 发现站点，去重后逐站获取最近 48 小时的小时观测；进度保存在 `data/live/metoffice_national_bootstrap.json`，中断后可重跑。已发现且有观测的站点进入常规每日采集；中心与四方锚点仍每日检查伦敦附近站点。九项天气字段按官方样本解析；响应不含单位时，数据库单位留空，不做假定。

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

可重新下载并导入当前年度（在项目根目录运行）：

```powershell
curl.exe -L --retry 3 --retry-all-errors -o data\live\imports\westminster_2026.zip 'https://www.westminster.gov.uk/media/document/wcc-air-quality-sensor-export-2026'
& 'C:\path\to\python.exe' tools\import_breathe_wcc.py --year 2026
& 'C:\path\to\python.exe' tools\collect_live.py status
```

其他年份可将命令中的年份及 `--year` 同步改为 2021–2025。导入器只选择原表 `source=Breathe London` 的 PM₂.₅、NO₂小时浓度，保留原始 ZIP 校验值，重复导入可去重，修订会留旧版本。Westminster CSV 的时间没有时区标识；从春季夏令时缺失的 01:00 小时推断为伦敦本地时钟，`time_basis` 明确标记这一推断。秋季回拨时无法唯一对应 UTC 的 01:00 记录被跳过，不会被随意指定为某个小时。该导出没有逐值审定标记，质量状态为 `U`。使用时注明数据来自 [Breathe London](https://www.breathelondon.org/) 和 Westminster 市政府。

首次部署后至少观察 24 小时：运行 `status`，确认 LAQN/TfL 的最近成功时间持续前进；在任务计划程序查看上次结果与下一次运行时间；检查 `fetch_runs` 的失败、429 和耗时，并核对 `measurements` 的唯一键、最新真实观测时间和 `measurement_versions`。有密钥的来源在实际联通验收前会一直显示 `pending_credentials`。


## 地图与联动分析

城市总览采用本地固定版本 MapLibre GL JS 5.6.2 与 OpenFreeMap 浅色 Positron 底图，图表采用 ECharts 5.6.0。伦敦行政边界使用项目提供的 GeoJSON 叠加；河流专题图层已从当前页面移除。自行车使用本地 Supercluster 8.0.1 生成聚合标记：缩放级别小于 13 时以可点击的数字圆圈展示，数字为站点数；放大后显示全部原始站点，可查看车辆与空位。聚合标记通过 DOM 绘制；聚合组件加载失败时直接显示原始点位。更新数据、历史回放和图层开关均同步更新标记。第三方代码及许可证在 `assets/vendor/`。底图需要网络连接；底图资源失败时仍可查询业务数据、图表与表格。

地图和分析使用最近 48 小时的观测。进入历史回放时固定查询窗口，按所选小时的结束时刻取值，窗口末端以进入回放时的时间为上限。LAQN 小时值优先采用来源小时数据，缺少小时值时至少使用 3 个不同的 15 分钟观测求均值。返回的 `sample_count` 与 `aggregation` 保留计算依据。地图最新状态使用 LAQN 的 15 分钟浓度及 Breathe London 官网观测。地图圆点为 LAQN、菱形为 Breathe London。浓度按窗口内的数据分级，灰色代表缺测。

自行车每次成功采集都会写入 `bike_snapshots`，记录获取时间、来源修改时间、车辆、空位和容量。启动服务时从过去 48 小时的成功原始响应补回历史快照，恢复进度保存在 `traffic_archive_imports`。道路列表同样保留成功快照，以准确恢复某次列表中的事件范围和状态，并继续保存原有事件版本。回放采用游标之前最近一次完整快照，超过 10 分钟显示间断。原始档案缺失的区间保持缺测。

选择监测站后显示周围 500 米自行车站和 1 公里道路事件，可切换为 500 米、1 公里或 2 公里。自行车站关联 1 公里内最近的有值污染监测点。道路范围距离使用实际几何；区域气象仅采用 `area=London / Greater London` 的有效站点，默认选择所选时间范围有效观测最多者。气象缺少精确站点坐标和来源单位，界面显示区域、geohash 和单位缺失状态。同 UTC 小时配对至少 24 对且两列均有变化时计算 Pearson 相关系数，作为观察性分析。

分析接口为 `/api/map`、`/api/analysis/series`、`/api/analysis/nearby`、`/api/analysis/weather`。共同参数 `start`、`end`、`cursor` 使用带时区 ISO 时间，`start <= t < end`，回放数据满足 `t <= cursor`。`source` 可选 `laqn`、`breathe`、`breathe_wcc`。`metric` 支持 `PM25`、`NO2`、`PM10`、`O3` 四种浓度指标。附近查询通过 `kind=air / bike / road` 与 `site` 指定对象，或通过 `kind=point` 与 `lon`、`lat` 指定坐标。`radius`、`road_radius` 控制资源统计范围，`local_air` 返回地点实测、估算、最近站点读数或缺测状态。气象接口使用 `weather_site` 指定伦敦站点，series 接口使用逗号分隔的 `sites` 对比最多 4 站。

附近查询设置 `include_air_metrics=1` 时，`local_air_by_metric` 按污染物返回四项地点读数。气象查询设置 `context_only=1` 时，返回伦敦站点编号 `station`、站名 `station_name` 及时间窗口内最新的一组气象观测 `latest`。

智慧出行使用 `POST /api/journeys/plan`，请求体含 `[经度,纬度]` 的 `start`、`end`，`mode=shared_bike / walk / personal_bike`，以及 `max_walk_m=1000 / 2000`。服务器只读取最新 TfL BikePoint 快照，超过 15 分钟时不推荐取还车站。取车站需要至少 1 辆车，还车站需要至少 1 个空位。道路路线由 openrouteservice 的步行与骑行服务计算，需在启动服务的进程中设置 `ORS_API_KEY`。密钥保存在服务端。

响应的 `context.route_air` 按实际道路折线返回有序分块与污染物汇总。服务以每个分块的中点估计浓度，每块通常不超过 250 米，长路线最多取 120 块。LAQN 和 Breathe London 分别使用近三小时的监测数据计算，覆盖率按有估计结果的分块长度占全程距离的比例计算。沿途估计、监测依据和 Defra 的 Greater London DAQI 地区等级可在「出行环境参考」查看。路线服务失败时，该页签仍显示 DAQI 状态。
