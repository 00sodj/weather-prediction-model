# -*- coding: utf-8 -*-
"""
天气预测模型 - ARIMA 时间序列预测
功能：加载过去一年气温数据，使用ARIMA模型预测未来3天温度
目标：MAE ≤ 2°C

本模块负责 337 个城市的建模，原先是整条流水线里最重的一段（串行约 730s），
做了两件事把它压到 10 秒量级：

1. 进程级并行 + 每进程单线程 BLAS
   各城市完全独立，用 multiprocessing 分配到多核。但 numpy/statsmodels 底层
   BLAS 默认自己就会开多线程，和进程池抢核 —— 实测 4 进程只能快 2.42x；
   把每进程 BLAS 锁死到 1 线程后升到 3.12x。这几个环境变量**必须在
   import numpy 之前设置**，之后设置不生效。

2. 阶数缓存
   每次运行对每个城市搜索 10 组候选 (p,d,q)，占了 ARIMA 计算的绝大部分。
   而气温序列的最优阶数非常稳定，没必要天天重搜。阶数记在
   model_outputs/order_cache.json，每个城市各自计时，超过 REFRESH_DAYS 天才
   重新搜索。日常运行只需 1 次拟合而非 11 次。

注意：整条流水线的瓶颈其实不在本模块，而在数据采集（见 run_predict.py）。
"""

import os

# 必须在 import numpy / statsmodels 之前设置，见模块开头说明
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_var] = "1"

import json
import warnings
from datetime import date, datetime, timedelta, timezone
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
from sklearn.metrics import mean_absolute_error, mean_squared_error

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "weather_data")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "model_outputs")
ORDER_CACHE = os.path.join(OUTPUT_DIR, "order_cache.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FORECAST_DAYS = 3
TEST_DAYS = 30
REFRESH_DAYS = 7        # 阶数缓存有效期（天）
FULL_SEARCH = False     # True 则改用 AIC 网格搜索，慢很多

ARIMA_CANDIDATES = [
    (1, 1, 1), (2, 1, 1), (1, 1, 2), (2, 1, 2), (3, 1, 1),
    (1, 1, 3), (5, 1, 0), (0, 1, 5), (3, 1, 2), (2, 1, 3),
]


def load_data():
    """加载过去一年逐日气温数据"""
    csv_path = os.path.join(DATA_DIR, "过去一年气温数据_逐日.csv")
    df = pd.read_csv(csv_path)
    df["日期"] = pd.to_datetime(df["日期"])
    return df


def adf_test(series):
    """ADF平稳性检验"""
    result = adfuller(series.dropna(), autolag="AIC")
    return {
        "statistic": round(result[0], 4),
        "p_value": round(result[1], 6),
        "is_stationary": result[1] < 0.05,
        "critical_values": {k: round(v, 4) for k, v in result[4].items()},
    }


def find_best_arima_order(series, max_p=5, max_d=2, max_q=5):
    """通过AIC准则网格搜索最优ARIMA参数"""
    best_aic = np.inf
    best_order = (1, 1, 1)

    d_range = range(max_d + 1)
    adf = adf_test(series)
    if adf["is_stationary"]:
        d_range = [0, 1]
    else:
        d_range = [1, 2]

    for d in d_range:
        for p in range(max_p + 1):
            for q in range(max_q + 1):
                if p == 0 and q == 0:
                    continue
                try:
                    model = ARIMA(series, order=(p, d, q))
                    result = model.fit()
                    if result.aic < best_aic:
                        best_aic = result.aic
                        best_order = (p, d, q)
                except Exception:
                    continue

    return best_order, best_aic


def train_and_predict_city(df_city, city_name, cached_order=None, refresh_order=False):
    """对单个城市训练ARIMA模型并预测

    cached_order  : 上次选好的 (p,d,q)。命中且未过期时跳过候选搜索，
                    省掉 10/11 的拟合量。
    refresh_order : 强制重新搜索阶数（缓存缺失或已过期时由 main 传入）
    """
    df_city = df_city.sort_values("日期").reset_index(drop=True)
    df_city = df_city.set_index("日期")
    series = df_city["日均气温(°C)"].dropna()

    if len(series) < 60:
        return None

    train = series[:-TEST_DAYS]
    test = series[-TEST_DAYS:]

    adf_result = adf_test(train)

    if cached_order is not None and not refresh_order:
        best_order = tuple(cached_order)
        order_source = "cache"
    elif FULL_SEARCH:
        best_order, _ = find_best_arima_order(train, max_p=3, max_d=2, max_q=3)
        order_source = "grid"
    else:
        best_aic = np.inf
        best_order = (1, 1, 1)
        for order in ARIMA_CANDIDATES:
            try:
                m = ARIMA(train, order=order).fit()
                if m.aic < best_aic:
                    best_aic = m.aic
                    best_order = order
            except Exception:
                continue
        order_source = "search"

    model = ARIMA(series, order=best_order)
    fitted = model.fit()

    in_sample = fitted.fittedvalues

    forecast_result = fitted.get_forecast(steps=FORECAST_DAYS)
    forecast_mean = forecast_result.predicted_mean
    forecast_ci = forecast_result.conf_int(alpha=0.05)

    test_pred = fitted.predict(start=test.index[0], end=test.index[-1])

    mae = mean_absolute_error(test, test_pred)
    rmse = np.sqrt(mean_squared_error(test, test_pred))
    mape = np.mean(np.abs((test - test_pred) / test.replace(0, np.nan)).dropna()) * 100

    last_date = series.index[-1]
    forecast_dates = pd.date_range(start=last_date + timedelta(days=1), periods=FORECAST_DAYS)

    result = {
        "city": city_name,
        "order": list(best_order),
        "order_source": order_source,
        "aic": round(float(fitted.aic), 2),
        "adf_test": adf_result,
        "metrics": {
            "MAE": round(mae, 4),
            "RMSE": round(rmse, 4),
            "MAPE": round(mape, 2),
            "test_days": TEST_DAYS,
        },
        "history": {
            "dates": [d.strftime("%Y-%m-%d") for d in series.index],
            "values": [round(v, 2) for v in series.values],
        },
        "fitted": {
            "dates": [d.strftime("%Y-%m-%d") for d in in_sample.index],
            "values": [round(v, 2) if not np.isnan(v) else None for v in in_sample.values],
        },
        "test": {
            "dates": [d.strftime("%Y-%m-%d") for d in test.index],
            "actual": [round(v, 2) for v in test.values],
            "predicted": [round(v, 2) for v in test_pred.values],
        },
        "forecast": {
            "dates": [d.strftime("%Y-%m-%d") for d in forecast_dates],
            "values": [round(v, 2) for v in forecast_mean.values],
            "lower": [round(v, 2) for v in forecast_ci.iloc[:, 0].values],
            "upper": [round(v, 2) for v in forecast_ci.iloc[:, 1].values],
        },
    }
    return result


def load_order_cache():
    """读取阶数缓存。缓存损坏时返回空字典让本次重新搜索，不影响主流程。"""
    if not os.path.isfile(ORDER_CACHE):
        return {}
    try:
        with open(ORDER_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"  阶数缓存读取失败，本次将重新搜索: {exc}")
        return {}
    return data.get("cities", {}) if isinstance(data, dict) else {}


def save_order_cache(entries):
    payload = {
        "_updated": (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d"),
        "refresh_days": REFRESH_DAYS,
        "cities": entries,
    }
    with open(ORDER_CACHE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)


_GROUPS = None      # 子进程内：{城市: DataFrame}


def _worker_init():
    """子进程启动时各加载一次数据。

    Windows / macOS 用 spawn 启动子进程，父进程的全局变量不会继承，
    必须在这里重新读 CSV。放进 initializer 是为了每个子进程只读一次，
    而不是每个任务读一次。
    """
    global _GROUPS
    _GROUPS = {c: g.copy() for c, g in load_data().groupby("城市")}


def _worker_task(task):
    city, cached_order, refresh = task
    try:
        result = train_and_predict_city(
            _GROUPS[city], city, cached_order=cached_order, refresh_order=refresh
        )
        return city, result, None
    except Exception as exc:
        return city, None, f"{type(exc).__name__}: {exc}"


def main():
    print("加载数据...")
    df = load_data()
    cities = sorted(df["城市"].unique())
    print(f"共 {len(cities)} 个城市")

    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()   # 北京日期
    cache = load_order_cache()

    tasks, hits = [], 0
    for city in cities:
        entry = cache.get(city) or {}
        order = entry.get("order")
        if order:
            try:
                age = (today - date.fromisoformat(entry["selected"])).days
            except (KeyError, TypeError, ValueError):
                age = REFRESH_DAYS      # 记录损坏，当作过期重新搜索
            if age < REFRESH_DAYS:
                tasks.append((city, order, False))
                hits += 1
                continue
        tasks.append((city, None, True))

    workers = min(cpu_count() or 1, len(tasks))
    print(f"阶数缓存命中 {hits}/{len(cities)}，并行进程 {workers}\n")

    all_results, mae_list, new_cache = {}, [], dict(cache)
    success = 0

    def handle(done, city, result, err):
        nonlocal success
        if err:
            print(f"[{done}/{len(tasks)}] {city} ERROR: {err}")
            return
        if not result:
            print(f"[{done}/{len(tasks)}] {city} SKIP（有效数据不足 60 天）")
            return
        all_results[city] = result
        mae = result["metrics"]["MAE"]
        mae_list.append(mae)
        success += 1
        src = "缓存" if result["order_source"] == "cache" else "搜索"
        status = "OK" if mae <= 2.0 else f"MAE>{mae:.2f}"
        print(f"[{done}/{len(tasks)}] {city} ARIMA{tuple(result['order'])} "
              f"MAE={mae:.4f}°C [{status}] ({src})")
        # 复用缓存的保留原日期，让它继续按自己的节奏到期；重新搜索的刷新日期
        prev = cache.get(city) or {}
        selected = prev.get("selected") if result["order_source"] == "cache" else None
        new_cache[city] = {
            "order": result["order"],
            "selected": selected or today.isoformat(),
        }

    if workers > 1:
        with Pool(workers, initializer=_worker_init) as pool:
            for done, (city, result, err) in enumerate(
                    pool.imap_unordered(_worker_task, tasks, chunksize=4), 1):
                handle(done, city, result, err)
    else:
        _worker_init()
        for done, task in enumerate(tasks, 1):
            city, result, err = _worker_task(task)
            handle(done, city, result, err)

    save_order_cache(new_cache)

    print(f"\n{'='*60}")
    print(f"训练完成: {success}/{len(cities)} 成功")
    if mae_list:
        avg_mae = np.mean(mae_list)
        med_mae = np.median(mae_list)
        pct_good = sum(1 for m in mae_list if m <= 2.0) / len(mae_list) * 100
        print(f"平均 MAE: {avg_mae:.4f}°C")
        print(f"中位 MAE: {med_mae:.4f}°C")
        print(f"MAE≤2°C 占比: {pct_good:.1f}%")

    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    output_file = os.path.join(OUTPUT_DIR, "arima_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, cls=NpEncoder)
    print(f"\n结果保存: {output_file}")

    summary = {
        "total_cities": len(cities),
        "success": success,
        "avg_mae": round(np.mean(mae_list), 4) if mae_list else None,
        "median_mae": round(np.median(mae_list), 4) if mae_list else None,
        "pct_mae_le_2": round(pct_good, 1) if mae_list else None,
        "mae_distribution": {
            "le_1": sum(1 for m in mae_list if m <= 1.0),
            "le_1.5": sum(1 for m in mae_list if m <= 1.5),
            "le_2": sum(1 for m in mae_list if m <= 2.0),
            "le_3": sum(1 for m in mae_list if m <= 3.0),
            "gt_3": sum(1 for m in mae_list if m > 3.0),
        }
    }
    summary_file = os.path.join(OUTPUT_DIR, "training_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"摘要保存: {summary_file}")

    print("\n" + "=" * 60)
    print("[额外] 生成24小时气象数据分析...")
    hourly_analysis = analyze_24h_data()
    if hourly_analysis:
        hourly_file = os.path.join(OUTPUT_DIR, "hourly_analysis.json")
        with open(hourly_file, "w", encoding="utf-8") as f:
            json.dump(hourly_analysis, f, ensure_ascii=False, indent=2, cls=NpEncoder)
        print(f"24h分析保存: {hourly_file}")


def analyze_24h_data():
    """分析过去24小时的六项气象数据"""
    csv_path = os.path.join(DATA_DIR, "过去24小时气象数据_逐小时.csv")
    if not os.path.isfile(csv_path):
        print("  24h数据文件不存在，跳过")
        return None
    df = pd.read_csv(csv_path)
    df["时间"] = pd.to_datetime(df["时间"])

    PARAMS = ["气温(°C)", "相对湿度(%)", "气压(hPa)", "降水(mm)", "风速(km/h)", "风向(°)"]
    param_en = {
        "气温(°C)": "temperature", "相对湿度(%)": "humidity",
        "气压(hPa)": "pressure", "降水(mm)": "precipitation",
        "风速(km/h)": "wind_speed", "风向(°)": "wind_direction",
    }

    result = {}
    cities = sorted(df["城市"].unique())
    for city in cities:
        dc = df[df["城市"] == city].sort_values("时间")
        city_data = {
            "times": [t.strftime("%Y-%m-%d %H:%M") for t in dc["时间"]],
        }
        for p in PARAMS:
            key = param_en[p]
            vals = dc[p].tolist()
            clean = [v for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))]
            city_data[key] = {
                "values": [round(v, 2) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None for v in vals],
                "stats": {
                    "mean": round(np.mean(clean), 2) if clean else None,
                    "max": round(max(clean), 2) if clean else None,
                    "min": round(min(clean), 2) if clean else None,
                    "std": round(np.std(clean), 2) if clean else None,
                }
            }
        result[city] = city_data

    print(f"  分析了 {len(result)} 个城市的24h数据")
    return result


if __name__ == "__main__":
    main()
