# 天气预测模型

基于历史气象数据与 ARIMA 时间序列模型，对全国 **337 个城市**
（含直辖市、地级市、自治州、盟）预测未来 3 天日均气温，
并生成一份单文件的交互式可视化报告。

## 模型效果

| 指标 | 数值 |
|---|---|
| 覆盖城市 | 337 |
| 平均 MAE | **1.00 °C** |
| MAE 中位数 | 0.99 °C |
| MAE ≤ 2 °C 的城市占比 | **99.7 %** |

评价方式：每个城市取最后 30 天作为留出集，比较预测值与真实气温的平均绝对误差。
数据每天更新，以上数值会随之小幅波动。

---

## 快速开始

### 方式零：直接在线看（什么都不用装）

**https://00sodj.github.io/weather-prediction-model/**

报告已通过 GitHub Pages 发布，浏览器打开这个链接就能看到完整预测结果，
不需要下载、不需要装 Python。在手机上也能正常浏览。

### 方式一：下载到本地看（零安装）

先 `git clone` 或下载 ZIP，然后双击 `mox/天气温度预测系统.html`，或者把它拖进浏览器。

这个文件是**单文件自包含**的 —— 一年历史数据、预测结果、图表配置全部内嵌在 HTML 里，
不需要装 Python，也不需要装任何依赖。唯一要求是联网（图表库 ECharts 走 CDN 加载）。

### 方式二：重新跑一遍完整流程

**环境要求：Python 3.9 或更高**

```bash
cd mox
pip install -r requirements.txt
python run_predict.py
```

Windows 下也可以直接双击 `mox/一键运行.bat`，它会自动检测 Python 环境、
检查依赖是否齐全（缺了才安装）、跑完整个流程，最后自动打开报告页面。

---

## 运行流程

脚本依次输出 `[1/4]` ~ `[4/4]` 四个阶段：

1. **增量补采逐日气温，并补齐历史缺口**
   - 用 Forecast API 补最近 7 天到今天。这里不能用 Archive API —— 它有 2～5 天延迟，
     会拿不到最新气温，导致预测基准日错误。
   - 紧接着逐城市检查 370 天窗口。**只查全局日期集合是不够的**：337 个城市里只要还有
     1 个城市缺某一天，那天就不会从全局集合里消失，缺口会被当成完整数据放过，
     所以必须按城市逐一比对。缺失区间按时间远近分别走 Archive / Forecast API，
     遇 429 自动退避重试，多轮收敛直到逐城市无缺口。

2. **补采 24 小时逐小时数据**
   气温、相对湿度、气压、降水、风速、风向六项指标。

3. **ARIMA 建模预测**
   每个城市先做 ADF 平稳性检验确定差分阶数 `d`；从 10 组候选 `(p, d, q)` 中按 AIC
   准则选最优（命中阶数缓存时跳过这步）；拟合全序列后向前预测 3 天；
   用最后 30 天作为留出集计算 MAE / RMSE / MAPE。

4. **生成 HTML 报告**
   把结果渲染成单文件页面。

## 性能设计

337 个城市的建模与采集是两条独立的耗时线，各自做了针对性处理。

### 数据采集：批次并发

逐日气温、逐小时气象、历史补缺的 HTTP 批次彼此独立，统一用
`ThreadPoolExecutor` 并发 —— `requests` 等待响应时会释放 GIL，
线程池可以把等待时间完全重叠起来。

并发会触发 Open-Meteo 限流，所以**并发与重试成对出现**：遇 429 按 `Retry-After`
退避重试，避免整批城市被静默丢弃（逐小时 CSV 是覆盖式写入，丢一批就等于
整体缺几十个城市）。批次大小按请求权重自适应：长区间拆小防限流，短区间合并减少请求数。

### ARIMA 训练：进程并行 + 阶数缓存

**(a) 进程级并行 + 每进程单线程 BLAS**

各城市互不依赖，用 `multiprocessing.Pool` 分配到多核。关键在于每进程锁死单线程 BLAS：

```python
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_var] = "1"          # 必须在 import numpy 之前
```

numpy / statsmodels 底层 BLAS 默认自己就会开多线程，和进程池抢核；锁成每进程 1 线程
才能让并行真正生效。这几个环境变量在 `import numpy` 之后再设是无效的。

**(b) 阶数缓存（`model_outputs/order_cache.json`）**

重搜 10 组候选阶数占了 ARIMA 计算的大部分，而气温序列的最优 `(p, d, q)` 非常稳定。
阶数缓存下来，**每个城市各自按 7 天计时**，超期才重新搜索 —— 刷新天然错开，
不会出现"每周某一天突然变慢"。日常运行只需 1 次拟合而非 11 次。

### 实测耗时

| 阶段 | 耗时 |
|---|---|
| 数据采集（本地） | 约 10 s |
| ARIMA 训练（本地） | 约 16 s |
| 完整流程（本地） | 约 50 s |
| 完整工作流（CI） | 约 1～2 分钟 |

并行与缓存只影响耗时，不改变计算结果（MAE 三项指标完全一致）。

## 自动更新

仓库配置了 GitHub Actions 工作流（`.github/workflows/update-report.yml`），
**每天北京时间 07:00** 自动完成：采集数据 → 补齐缺口 → 训练 ARIMA →
重新生成报告 → 提交推送。GitHub Pages 随后自动重新发布，
所以上面那个在线地址始终显示最新数据，不需要任何人工操作。

也可以手动触发：在仓库的 Actions 页面点 `Run workflow`，
或推送 `mox/` 目录下的任何改动（改代码后报告会自动跟着更新）。

> 自动提交会**覆盖**上一次的自动提交（`git commit --amend` + 强推），
> 否则每天约 20MB 的数据提交会很快把仓库撑大。
> 注意：这个覆盖只在最新提交本身就是自动提交时生效；如果你刚推过代码，
> 机器人会新建一条而不是覆盖，所以历史里会留有多条 `chore:` 提交。

---

## 目录结构

```
.
├── .github/workflows/
│   └── update-report.yml              # 每日自动更新工作流
├── .gitattributes                     # 锁定 *.bat 原样存储（CRLF）
├── mox/
│   ├── fetch_weather_data.py          # 全量采集（首次运行 / 数据缺失时用）
│   ├── fetch_missing_v2.py            # 补采「数据不足 60 天」的城市
│   ├── backfill_missing_days.py       # 逐城市补齐日期缺口（会被 run_predict 调用）
│   ├── run_predict.py                 # 一键流程入口
│   ├── weather_predict.py             # ARIMA 建模与预测
│   ├── generate_standalone_html.py    # 生成单文件 HTML 报告
│   ├── 一键运行.bat                    # Windows 一键脚本
│   ├── requirements.txt
│   ├── weather_data/                  # 气象数据（CSV）
│   │   ├── 过去一年气温数据_逐日.csv
│   │   └── 过去24小时气象数据_逐小时.csv
│   ├── model_outputs/                 # 模型结果（JSON）
│   │   ├── arima_results.json         # 各城市的建模结果与预测
│   │   ├── hourly_analysis.json       # 24 小时气象分析
│   │   ├── training_summary.json      # 训练摘要指标
│   │   └── order_cache.json           # ARIMA 阶数缓存
│   └── 天气温度预测系统.html            # 可视化报告
├── index.html                         # GitHub Pages 入口（自动跳转到报告）
├── README.md
└── 系统架构图.png
```

---

## 数据来源

[Open-Meteo](https://open-meteo.com) 公共 API —— **免费，不需要申请 API Key**。

| 用途 | 端点 |
|---|---|
| 近期 / 预报数据 | `api.open-meteo.com/v1/forecast` |
| 历史归档数据 | `archive-api.open-meteo.com/v1/archive` |

时区统一使用 `Asia/Shanghai`。

---

## 技术栈

Python 3.9+ · pandas · numpy · statsmodels（ARIMA / ADF）· scikit-learn（MAE 评估）
· requests · `multiprocessing` / `concurrent.futures`（并行与并发）· ECharts 5

---

## 说明

- `mox/model_outputs/` 里的 JSON 是已经训练好的结果。**如果只想改报告样式**，
  单独运行 `generate_standalone_html.py` 就够了 —— 它只读这几个 JSON，
  不联网、也不需要原始数据。
- 重新训练前请确认 `mox/weather_data/过去一年气温数据_逐日.csv` 存在；
  若不存在，先跑 `fetch_weather_data.py` 做一次全量采集（数据量较大，需要几分钟）。
- 城市坐标表写在 `fetch_weather_data.py` 的 `CITIES` 常量里，想增删城市直接改这里。

### 排障：双击 `一键运行.bat` 刷出一堆碎片报错

如果窗口里出现这类信息：

```
'el' 不是内部或外部命令，也不是可运行的程序
'quirements.txt' 不是内部或外部命令，也不是可运行的程序
```

说明这个 `.bat` 文件变成了 **LF 换行**。cmd.exe 解析批处理时按字节偏移定位命令，
LF-only 会让偏移错位，把它切成上面那样的碎片。

本仓库已用 `.gitattributes` 把 `*.bat` 锁定为原样存储（`-text`），
从 GitHub 下载的 ZIP 里应该是 CRLF，正常不会出现。若仍然遇到，
说明下载到的还是旧版本，重新下载一次即可；或者改用 `git clone`。
