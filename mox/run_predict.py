# -*- coding: utf-8 -*-
"""
一键预测脚本：增量更新数据 + ARIMA预测 + 生成HTML
每次运行自动补采最近数据到今天，确保预测的是未来3天

采集部分是主要耗时来源（48 个 HTTP 请求），因此所有批次都走线程池并发 ——
requests 在等待响应时释放 GIL，线程池就能把等待时间重叠起来。
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

# 否则输出被块缓冲，日志里所有时间戳都会堆在进程退出的那一刻，
# 完全看不出各阶段实际耗时（CI 上排查问题时吃过这个亏）
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "weather_data")
CSV_PATH = os.path.join(DATA_DIR, "过去一年气温数据_逐日.csv")
HOURLY_CSV = os.path.join(DATA_DIR, "过去24小时气象数据_逐小时.csv")

sys.path.insert(0, SCRIPT_DIR)
from fetch_weather_data import CITIES, PARAM_CN, HOURLY_PARAMS
from backfill_missing_days import backfill

DAILY_PARAMS = "temperature_2m_mean,temperature_2m_max,temperature_2m_min"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

BATCH_SIZE = 50
WORKERS = 4              # 并发太高会触发 Open-Meteo 限流
MAX_RETRY = 6


def _get_json(params, max_retry=MAX_RETRY):
    """带 429 退避的 GET。

    Open-Meteo 的额度按「城市数 × 天数 × 变量数」计权重，并发稍高就会限流。
    没有重试的话这一批城市会被静默丢掉 —— 而逐小时 CSV 是覆盖式写入，
    于是 337 城会悄悄变成 300 城。
    """
    for attempt in range(max_retry):
        try:
            resp = requests.get(FORECAST_URL, params=params, timeout=90)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 0) or 0) or min(4 * (attempt + 1), 40)
                print(f"    429 限流，{wait}s 后重试（第 {attempt + 1}/{max_retry} 次）")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt == max_retry - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"429 重试 {max_retry} 次仍失败")


def fetch_batches(cities, block_key, param_str, extra, time_col, label=""):
    """并发拉取全部批次，返回行列表。

    每个批次一次 HTTP 请求，彼此完全独立。串行时等待时间无法重叠，
    48 个请求能拖到十几分钟；并发后总耗时约等于最慢的那个请求。
    """
    batches = [cities[i:i + BATCH_SIZE] for i in range(0, len(cities), BATCH_SIZE)]

    def one(batch):
        params = {
            "latitude": ",".join(str(c[1]) for c in batch),
            "longitude": ",".join(str(c[2]) for c in batch),
            block_key: param_str,
            "timezone": "Asia/Shanghai",
        }
        params.update(extra)
        data = _get_json(params)

        rows = []
        for j, city_data in enumerate(data if isinstance(data, list) else [data]):
            block = city_data.get(block_key, {})
            for k, ts in enumerate(block.get("time", [])):
                row = {"城市": batch[j][0], time_col: ts}
                for p in param_str.split(","):
                    vals = block.get(p, [])
                    row[PARAM_CN.get(p, p)] = vals[k] if k < len(vals) else None
                rows.append(row)
        return rows

    rows, failed = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(one, b): i for i, b in enumerate(batches)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                rows.extend(fut.result())
            except Exception as exc:
                failed += 1
                print(f"    {label}批次 {idx + 1}/{len(batches)} 失败: {exc}")

    if failed:
        print(f"    ⚠ {label}共 {failed}/{len(batches)} 个批次失败，数据可能不完整！")
    return rows


def update_daily_data():
    """增量补采最近7天数据到今天（用Forecast API，不受Archive延迟影响）"""
    print("=" * 50)
    print("[1/4] 增量更新逐日气温数据...")
    t0 = time.time()

    if not os.path.isfile(CSV_PATH):
        print("  错误：基础数据文件不存在，请先运行 fetch_weather_data.py")
        return False

    df_exist = pd.read_csv(CSV_PATH)
    df_exist["日期"] = pd.to_datetime(df_exist["日期"])
    last_date = df_exist["日期"].max()
    print(f"  现有数据最新日期: {last_date.strftime('%Y-%m-%d')}")

    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()   # 北京日期
    today_str = today.strftime("%Y-%m-%d")
    start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    print(f"  补采范围: {start_date} ~ {today_str}")

    all_rows = fetch_batches(
        CITIES, "daily", DAILY_PARAMS,
        {"start_date": start_date, "end_date": today_str}, "日期",
    )

    if all_rows:
        df_new = pd.DataFrame(all_rows)
        df_new["日期"] = pd.to_datetime(df_new["日期"])
        df_combined = pd.concat([df_exist, df_new], ignore_index=True)
        df_combined = df_combined.sort_values(["城市", "日期"])
        df_combined = df_combined.drop_duplicates(subset=["城市", "日期"], keep="last")
        # 截断点必须用「北京日期」而不是带时分秒的 now。用 now 会让最早那天
        # 落在截断点之前被丢掉，下一次 backfill 又把它补回来 ——
        # 每天白跑一轮补采，CSV 长度还在 370/371 之间来回震荡。
        one_year_ago = pd.Timestamp(today - timedelta(days=370))
        df_combined = df_combined[df_combined["日期"] >= one_year_ago]
        df_combined["日期"] = df_combined["日期"].dt.strftime("%Y-%m-%d")
        df_combined.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        new_last = pd.to_datetime(df_combined["日期"]).max()
        print(f"  更新后数据: {len(df_combined)} 条, {df_combined['城市'].nunique()} 城市")
        print(f"  最新日期: {new_last.strftime('%Y-%m-%d')}")
    else:
        print("  警告：未能获取新数据")
    print(f"  耗时 {time.time() - t0:.0f}s")

    # 上面只补了最近 7 天。若距上次运行超过 7 天，或历史数据本身有洞，
    # 序列会留下空档，ARIMA 会把带洞的序列当连续序列训练，必须补齐。
    print()
    print("  检查历史缺口...")
    t1 = time.time()
    try:
        filled_rows, gap_count = backfill(verbose=True)
        if gap_count:
            print(f"  → 已处理 {gap_count} 处缺口，补入 {filled_rows} 条")
    except Exception as exc:
        print(f"  缺口补采失败（不影响主流程）: {exc}")
    print(f"  耗时 {time.time() - t1:.0f}s")

    return True


def update_hourly_data():
    """更新24小时气象数据"""
    print()
    print("=" * 50)
    print("[2/4] 更新24小时气象数据...")
    t0 = time.time()

    all_rows = fetch_batches(
        CITIES, "hourly", HOURLY_PARAMS,
        {"past_hours": 24, "forecast_hours": 0}, "时间",
    )

    if all_rows:
        df = pd.DataFrame(all_rows)
        df.to_csv(HOURLY_CSV, index=False, encoding="utf-8-sig")
        print(f"  已更新: {len(df)} 条, {df['城市'].nunique()} 城市")
    else:
        print("  警告：未能获取24h数据")
    print(f"  耗时 {time.time() - t0:.0f}s")


def run_predict():
    """运行ARIMA预测"""
    print()
    print("=" * 50)
    print("[3/4] 运行ARIMA预测...")
    import subprocess
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "weather_predict.py")], check=True)


def run_html():
    """生成HTML报告"""
    print()
    print("=" * 50)
    print("[4/4] 生成HTML报告...")
    import subprocess
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "generate_standalone_html.py")], check=True)


if __name__ == "__main__":
    start = time.time()
    print("城市温度预测系统 - 一键运行")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    ok = update_daily_data()
    if not ok:
        print("数据更新失败，退出")
        sys.exit(1)

    update_hourly_data()
    run_predict()
    run_html()

    elapsed = time.time() - start
    print()
    print("=" * 50)
    print(f"全部完成！耗时 {elapsed:.0f} 秒")
    print("预测结果已更新为未来3天")
