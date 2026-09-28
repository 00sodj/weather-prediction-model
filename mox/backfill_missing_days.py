# -*- coding: utf-8 -*-
"""
补齐逐日气温数据中的日期缺口

run_predict.py 的增量更新只补最近 7 天。如果两次运行间隔超过 7 天，
或者历史数据本身就有洞，序列里就会留下空档 —— ARIMA 会把带洞的序列
当成连续序列来训练，历史曲线也会出现断层。

设计要点：

1. **逐城市检查**，而不是只看全局日期集合。
   337 个城市里只要有 1 个城市缺某天，那天就不会从全局集合里消失 ——
   只查全局会把「75 个城市少 97 天」这种问题当成完整数据放过。

2. **按城市缺失区间签名分组**，把缺口相同的城市合并成一次请求。

3. **遇到 429 限流自动退避重试**，并遵循响应里的 Retry-After。
   Open-Meteo 免费额度按「城市数 × 天数 × 变量数」计权重，
   补长区间很容易触发限流，没有重试就会静默丢数据。

4. 多轮收敛：一轮补完再查一遍，直到逐城市都完整或达到轮次上限。

可单独运行，也会被 run_predict.py 调用。
"""
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "weather_data")
CSV_PATH = os.path.join(DATA_DIR, "过去一年气温数据_逐日.csv")

sys.path.insert(0, SCRIPT_DIR)
from fetch_weather_data import CITIES, PARAM_CN

DAILY_PARAMS = "temperature_2m_mean,temperature_2m_max,temperature_2m_min"
WINDOW_DAYS = 370
ARCHIVE_LAG = 6          # Archive API 约 5 天延迟

# 批次大小按区间长度自适应：Open-Meteo 的额度按「城市数 × 天数 × 变量数」
# 计权重，补 97 天这种长区间用大批次很容易 429，短区间则没必要拆太碎。
BATCH_SIZE_LONG = 10
BATCH_SIZE_SHORT = 50
LONG_RANGE_DAYS = 30
WORKERS = 4              # 批次之间并发数
MAX_RETRY = 6
MAX_ROUNDS = 5
COLUMNS = ["城市", "日期", "日均气温(°C)", "日最高气温(°C)", "日最低气温(°C)"]

COORD = {name: (lat, lon) for name, lat, lon in CITIES}


def today_bj():
    """北京时间的今天。

    不用 zoneinfo —— Windows 上的 Python 默认不带 IANA 时区库，
    ZoneInfo("Asia/Shanghai") 会直接抛异常。固定 +8 偏移最稳。
    """
    t = datetime.now(timezone.utc) + timedelta(hours=8)
    return t.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def to_ranges(dates):
    """把日期列表压成连续区间 [(start, end), ...]"""
    dates = sorted(dates)
    if not dates:
        return []
    out, s, prev = [], dates[0], dates[0]
    for d in dates[1:]:
        if (d - prev).days == 1:
            prev = d
        else:
            out.append((s, prev))
            s = prev = d
    out.append((s, prev))
    return out


def per_city_missing(df, window_start, today):
    """返回 {城市: [缺失日期, ...]}，只含真正有缺口的城市"""
    expected = set(pd.date_range(window_start, today, freq="D"))
    result = {}
    for city, grp in df.groupby("城市"):
        have = {pd.Timestamp(d).normalize() for d in grp["日期"]}
        miss = expected - have
        if miss:
            result[city] = sorted(miss)
    return result


def _request(url, params, verbose=True):
    """带 429 退避的请求。失败返回 None。"""
    for attempt in range(MAX_RETRY):
        try:
            resp = requests.get(url, params=params, timeout=120)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 0) or 0) or min(5 * (attempt + 1), 60)
                if verbose:
                    print(f"      429 限流，{wait}s 后重试（第 {attempt + 1} 次）")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == MAX_RETRY - 1:
                if verbose:
                    print(f"      请求失败（已重试 {MAX_RETRY} 次）: {exc}")
                return None
            time.sleep(3 * (attempt + 1))
    return None


def fetch_cities_dates(city_names, start, end, use_archive, verbose=True):
    """为一组城市补采指定日期区间（批次并发）"""
    url = ("https://archive-api.open-meteo.com/v1/archive" if use_archive
           else "https://api.open-meteo.com/v1/forecast")
    tag = "Archive" if use_archive else "Forecast"
    size = BATCH_SIZE_LONG if (end - start).days > LONG_RANGE_DAYS else BATCH_SIZE_SHORT
    batches = [city_names[i:i + size] for i in range(0, len(city_names), size)]

    def one(batch):
        coords = [COORD[c] for c in batch]
        params = {
            "latitude": ",".join(str(c[0]) for c in coords),
            "longitude": ",".join(str(c[1]) for c in coords),
            "daily": DAILY_PARAMS,
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "timezone": "Asia/Shanghai",
        }
        data = _request(url, params, verbose)
        if data is None:
            return []
        rows = []
        for j, city_data in enumerate(data if isinstance(data, list) else [data]):
            daily = city_data.get("daily", {})
            for k, ts in enumerate(daily.get("time", [])):
                row = {"城市": batch[j], "日期": ts}
                for p in DAILY_PARAMS.split(","):
                    vals = daily.get(p, [])
                    row[PARAM_CN.get(p, p)] = vals[k] if k < len(vals) else None
                rows.append(row)
        return rows

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(one, b) for b in batches]
        for fut in as_completed(futures):
            try:
                rows.extend(fut.result())
            except Exception as exc:
                print(f"      [{tag}] 批次异常: {exc}")

    if verbose:
        print(f"      [{tag}] {len(city_names)} 城 × {start:%m-%d}~{end:%m-%d}："
              f"{len(batches)} 批 × {size} 城并发，取回 {len(rows)} 条")
    return rows


def _merge_and_save(df_old, new_rows, window_start):
    df_new = pd.DataFrame(new_rows)
    df_new["日期"] = pd.to_datetime(df_new["日期"])
    df_all = pd.concat([df_old, df_new], ignore_index=True)
    df_all = df_all.drop_duplicates(subset=["城市", "日期"], keep="last")
    df_all = df_all[df_all["日期"] >= window_start]
    df_all = df_all.sort_values(["城市", "日期"])
    df_all["日期"] = df_all["日期"].dt.strftime("%Y-%m-%d")
    df_all[COLUMNS].to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return df_all


def backfill(verbose=True, max_rounds=MAX_ROUNDS):
    """逐城市补齐缺口。返回 (补采行数, 处理的缺口组数)"""
    if not os.path.isfile(CSV_PATH):
        if verbose:
            print("  跳过：基础数据文件不存在")
        return 0, 0

    today = today_bj()
    window_start = today - timedelta(days=WINDOW_DAYS)

    total_rows = 0
    total_groups = 0

    for rnd in range(1, max_rounds + 1):
        df = pd.read_csv(CSV_PATH)
        df["日期"] = pd.to_datetime(df["日期"])
        missing = per_city_missing(df, window_start, today)

        if not missing:
            if verbose:
                note = "数据完整" if rnd == 1 else f"第 {rnd - 1} 轮后已补齐"
                print(f"  {note}（{window_start:%Y-%m-%d} ~ {today:%Y-%m-%d}，337 城逐日无缺口）")
            return total_rows, total_groups

        # 按「缺失区间签名」分组，把缺口相同的城市合并成一次请求
        groups = defaultdict(list)
        for city, dates in missing.items():
            groups[tuple(to_ranges(dates))].append(city)

        miss_days = sum(len(v) for v in missing.values())
        if verbose:
            print(f"  第 {rnd} 轮：{len(missing)} 城有缺口，共 {miss_days} 个城市日，"
                  f"归为 {len(groups)} 组")

        new_rows = []
        for sig, cities in groups.items():
            for start, end in sig:
                if verbose:
                    print(f"    · {len(cities)} 城 · {start:%Y-%m-%d} ~ {end:%Y-%m-%d}")
                use_archive = end <= today - timedelta(days=ARCHIVE_LAG)
                new_rows.extend(fetch_cities_dates(cities, start, end, use_archive, verbose))
                total_groups += 1

        if not new_rows:
            if verbose:
                print("  本轮未取回任何数据，停止")
            return total_rows, total_groups

        total_rows += len(new_rows)
        _merge_and_save(df, new_rows, window_start)

    # 收敛检查
    df = pd.read_csv(CSV_PATH)
    df["日期"] = pd.to_datetime(df["日期"])
    still = per_city_missing(df, window_start, today)
    if verbose:
        if still:
            n = sum(len(v) for v in still.values())
            print(f"  ⚠ 达到轮次上限，仍有 {len(still)} 城 / {n} 个城市日未补齐（下次运行会继续）")
        else:
            print("  缺口已全部补齐")
    return total_rows, total_groups


if __name__ == "__main__":
    print("补齐历史数据缺口（逐城市检查）")
    print("=" * 50)
    rows, cnt = backfill()
    print("=" * 50)
    print(f"结束：补采 {rows} 条，处理 {cnt} 个缺口组")
