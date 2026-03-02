#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
澳门巴士实时报站信息获取
通过 BIS 巴士报站系统 API 获取某路线的预计到达车辆
支持查询所有线路
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# BIS API 基础 URL（从 bis.dsat.gov.mo/macauweb 逆向得出）
BIS_BASE = "https://bis.dsat.gov.mo/macauweb"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Referer": "https://bis.dsat.gov.mo/macauweb/",
}
REQUEST_DELAY = 0.15  # 串行时的请求间隔
STATIC_FETCH_WORKERS = 6  # 并发获取静态数据的线程数
REALTIME_FETCH_WORKERS = 8  # 并发获取实时数据的线程数

# 缓存：静态数据 (route, dir) -> data；路线筛选 (start, end) -> [(route, dir), ...]
_static_cache: dict = {}
_routes_passing_cache: dict = {}

# 常用路线预设（起始站代码, 结束站代码, 起始站名称, 结束站名称），可用数字 1–6 快速选择
COMMON_ROUTES = [
    ("M109", "M127", "提督/高士德", "海邊新街"),           # 1
    ("M111", "M127", "昌明花園", "海邊新街"),             # 2
    ("M127", "M170/1", "海邊新街", "殷皇子馬路(1)"),      # 3
    ("M127", "M170/2", "海邊新街", "殷皇子馬路(2)"),      # 4
    ("M109", "M170/1", "提督/高士德", "殷皇子馬路(1)"),   # 5
    ("M111", "M170/2", "昌明花園", "殷皇子馬路(2)"),      # 6
]

# 硬编码：6 条预设路线经过的巴士 (route, direction)，直接跳过静态数据排查
PRESET_ROUTES_MAP = {
    ("M109", "M127"): [("26A", "0"), ("26", "0"), ("33", "0")],
    ("M111", "M127"): [("3", "0"), ("1", "0"), ("3X", "0"), ("6A", "0"), ("16", "0"), ("16S", "0"), ("N1B", "0")],
    ("M127", "M170/1"): [("26A", "0"), ("33", "0")],
    ("M127", "M170/2"): [("3", "0"), ("3X", "0"), ("101", "0"), ("N1B", "0")],
    ("M109", "M170/1"): [("26A", "0"), ("33", "0")],
    ("M111", "M170/2"): [("3", "0"), ("3X", "0"), ("N1B", "0")],
}


def _http_get(url: str, params: Optional[dict] = None, timeout: int = 15) -> dict:
    """使用 urllib 发起 GET 请求，返回 JSON 解析后的字典"""
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers=HEADERS, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ETA 本地估算参数（DSAT 澳门巴士：站间距约 200–350m，取平均 275m）
AVG_DISTANCE_KM_PER_STOP = 0.275
DWELL_SEC_PER_STOP = 30  # 每站停靠上下客约 20–45 秒
DEFAULT_SPEED_KMH = 15   # 无速度数据时的默认值（市区巴士）
ETA_BUFFER_MINUTES = 2.5  # 堵车/红绿灯缓冲，使预估更保守


def estimate_eta_minutes(stops: int, speed_kmh: Optional[float] = None) -> Optional[float]:
    """
    根据距目标站数和当前速度，估算到达该站的预计分钟数。
    公式：行驶时间 + 停靠时间（每站约 30 秒）

    Args:
        stops: 距目标站的站数
        speed_kmh: 当前速度 km/h，None 或 0 时使用默认 15 km/h

    Returns:
        预计分钟数，无法计算时返回 None
    """
    if stops is None or stops < 0:
        return None
    if stops == 0:
        return 0.0
    try:
        sp = float(speed_kmh) if speed_kmh is not None else DEFAULT_SPEED_KMH
    except (TypeError, ValueError):
        sp = DEFAULT_SPEED_KMH
    if sp <= 0:
        sp = DEFAULT_SPEED_KMH
    dist_km = stops * AVG_DISTANCE_KM_PER_STOP
    travel_min = (dist_km / sp) * 60
    dwell_min = stops * DWELL_SEC_PER_STOP / 60
    total = travel_min + dwell_min + ETA_BUFFER_MINUTES
    return round(total, 1)


def get_all_routes(lang: str = "tc") -> List[str]:
    """从 BIS 获取所有可用路线编号列表"""
    url = f"{BIS_BASE}/getRouteAndCompanyList.html"
    try:
        data = _http_get(url, timeout=15)
        h = data.get("header")
        if h == "000" or (isinstance(h, dict) and h.get("status") == "000"):
            route_list = data.get("data", {}).get("routeList", [])
            return [item.get("routeName", "") for item in route_list if item.get("routeName")]
    except Exception as e:
        print(f"获取路线列表失败: {e}")
    return []


def get_route_static_data(
    route_name: str, direction: str = "0", lang: str = "tc", use_cache: bool = True
) -> Optional[dict]:
    """获取路线静态数据（站点列表等），支持缓存"""
    cache_key = (route_name, direction)
    if use_cache and cache_key in _static_cache:
        return _static_cache[cache_key]
    url = f"{BIS_BASE}/getRouteData.html"
    params = {"action": "sd", "routeName": route_name, "dir": direction, "lang": lang}
    try:
        data = _http_get(url, params=params, timeout=15)
        h = data.get("header")
        if h == "000" or (isinstance(h, dict) and h.get("status") == "000"):
            result = data.get("data")
            if use_cache and result:
                _static_cache[cache_key] = result
            return result
    except Exception as e:
        print(f"获取路线数据失败: {e}")
    return None


def _fetch_static_and_check(
    route: str, direction: str, start_station: str, end_station: str
) -> Optional[Tuple[str, str]]:
    """获取静态数据并检查是否经过 A→B，返回 (route, dir) 或 None"""
    static = get_route_static_data(route, direction, use_cache=True)
    if not static or "routeInfo" not in static:
        return None
    route_static = static.get("routeInfo", [])
    for i, st in enumerate(route_static):
        if st.get("staCode") == start_station:
            for j in range(i + 1, len(route_static)):
                if route_static[j].get("staCode") == end_station:
                    return (route, direction)
            break
    return None


def get_routes_passing_stations(
    start_station: str, end_station: str, routes: Optional[List[str]] = None
) -> List[Tuple[str, str]]:
    """
    两阶段优化：先排查所有路线，只保留经过 A→B 的 (route, direction)。
    6 条预设路线使用硬编码，直接跳过静态数据排查；否则结果缓存。
    """
    cache_key = (start_station, end_station)
    if cache_key in PRESET_ROUTES_MAP:
        return list(PRESET_ROUTES_MAP[cache_key])
    if cache_key in _routes_passing_cache:
        return _routes_passing_cache[cache_key]

    if routes is None:
        routes = get_all_routes()
    if not routes:
        routes_file = Path(__file__).parent / "data" / "bus_routes.json"
        if routes_file.exists():
            with open(routes_file, encoding="utf-8") as f:
                routes = [r.get("route_no", "") for r in json.load(f) if r.get("route_no")]

    passing: List[Tuple[str, str]] = []
    tasks = []
    for route in routes:
        for direction in ("0", "1"):
            tasks.append((route, direction))

    def _task(t: Tuple[str, str]) -> Optional[Tuple[str, str]]:
        r, d = t
        return _fetch_static_and_check(r, d, start_station, end_station)

    with ThreadPoolExecutor(max_workers=STATIC_FETCH_WORKERS) as executor:
        futures = {executor.submit(_task, t): t for t in tasks}
        for future in as_completed(futures):
            if future.result():
                passing.append(future.result())

    _routes_passing_cache[cache_key] = passing
    return passing


def clear_route_caches() -> None:
    """清除路线筛选与静态数据缓存（路线调整后可调用）"""
    global _static_cache, _routes_passing_cache
    _static_cache.clear()
    _routes_passing_cache.clear()


def get_realtime_bus_data(route_name: str, direction: str = "0", lang: str = "tc") -> Optional[dict]:
    """获取路线实时巴士报站数据（每站预计到达的车辆）"""
    url = f"{BIS_BASE}/routestation/bus"
    params = {"action": "dy", "routeName": route_name, "dir": direction, "lang": lang}
    try:
        data = _http_get(url, params=params, timeout=15)
        h = data.get("header")
        if h == "000" or (isinstance(h, dict) and h.get("status") == "000"):
            return data.get("data")
    except Exception as e:
        print(f"获取实时数据失败: {e}")
    return None


def get_buses_from_a_to_b(
    route_name: str,
    station_a: str,
    station_b: str,
) -> dict:
    """
    查询 A 站到 B 站之间，从 A 开往 B 的车辆数量及每辆车的位置（位于哪两个站点之间）
    
    Args:
        route_name: 路线编号
        station_a: 起点站代码（如 M23）
        station_b: 终点站代码（如 M177）
    
    Returns:
        totalBuses: 车辆总数
        buses: 每辆车的车牌、位置（前一站与当前站）
    """
    # 先尝试方向 0，若 A 在 B 后面则用方向 1
    for direction in ("0", "1"):
        static = get_route_static_data(route_name, direction)
        realtime = get_realtime_bus_data(route_name, direction)
        if not realtime or "routeInfo" not in realtime:
            continue

        route_info = realtime.get("routeInfo", [])
        static_info = (static or {}).get("routeInfo", []) if static else []
        sta_name_map = {s.get("staCode"): s.get("staName", "") for s in static_info} if static_info else {}

        # 找 A、B 的索引（循环线可能有多处 A，取 A 在 B 前面的第一段）
        idx_a = idx_b = -1
        for i, st in enumerate(route_info):
            if st.get("staCode") == station_a:
                for j in range(i + 1, len(route_info)):
                    if route_info[j].get("staCode") == station_b:
                        idx_a, idx_b = i, j
                        break
                if idx_a >= 0:
                    break

        if idx_a < 0 or idx_b < 0:
            continue

        # 收集 A->B 路段内的车辆，并计算每辆车位于哪两站之间
        buses_with_position = []
        seen_plates = set()

        n = len(route_info)
        for i in range(idx_a, idx_b + 1):
            st = route_info[i]
            sta_code = st.get("staCode", "")
            sta_name = st.get("staName") or sta_name_map.get(sta_code, "")
            next_idx = (i + 1) % n
            next_code = route_info[next_idx].get("staCode", "")
            next_name = route_info[next_idx].get("staName") or sta_name_map.get(next_code, "")

            for bus in st.get("busInfo", []):
                plate = bus.get("busPlate", "")
                if plate and plate not in seen_plates:
                    seen_plates.add(plate)
                    status_val = str(bus.get("status", ""))
                    is_at_stop = status_val == "1"  # 1=停靠在当前站，0=当前站到下一站之间行驶
                    # 位置：在 当前站 与 下一站 之间，驶向下一站
                    buses_with_position.append({
                        "busPlate": plate,
                        "positionBetween": [sta_code, next_code],
                        "positionBetweenNames": [sta_name, next_name],
                        "currentStationIndex": i + 1,
                        "nextStationIndex": next_idx + 1,
                        "isAtStation": is_at_stop,
                        "speed": bus.get("speed", ""),
                        "status": status_val,
                    })

        st_a = route_info[idx_a]
        st_b = route_info[idx_b]
        return {
            "route": route_name,
            "direction": direction,
            "stationA": {"code": station_a, "name": st_a.get("staName") or sta_name_map.get(station_a, "")},
            "stationB": {"code": station_b, "name": st_b.get("staName") or sta_name_map.get(station_b, "")},
            "totalBuses": len(buses_with_position),
            "buses": buses_with_position,
        }

    return {"route": route_name, "error": "未找到 A→B 方向或无法获取数据", "totalBuses": 0, "buses": []}


def get_buses_by_stations_only(
    start_station: str,
    end_station: str,
    routes: Optional[List[str]] = None,
) -> dict:
    """
    仅输入起始站和结束站，返回：
    1) 即将到达起始站并会经过这两站的车
    2) 当前在这两站之间运行的车

    优化：两阶段查询——先排查所有路线筛出经过 A→B 的，再只对筛选结果查实时数据。
    """
    # 阶段一：只查静态数据，筛出经过 A→B 的 (route, direction)，结果缓存
    routes_to_check = get_routes_passing_stations(start_station, end_station, routes)

    approaching_start = []  # 即将到达起始站（index < idx_a）
    between_stations = []   # 在两站之间（idx_a < index < idx_b）
    sta_a_name = sta_b_name = ""
    segment_stations: List[dict] = []  # 起点到终点之间的所有站点（含起终点）
    # 按路线分别存储：每条路线有各自的站点序列（101、3、3X 走不同线路）
    _approaching_by_route: dict = {}  # route -> { "route_info", "idx_a", "sta_name_map", "buses" }

    # 阶段二：并发获取静态+实时数据
    def _fetch_route_data(rd: Tuple[str, str]) -> Optional[Tuple[str, str, dict, dict]]:
        route, direction = rd
        static = get_route_static_data(route, direction, use_cache=True)
        if not static or "routeInfo" not in static:
            return None
        route_static = static.get("routeInfo", [])
        idx_a = idx_b = -1
        for i, st in enumerate(route_static):
            if st.get("staCode") == start_station:
                for j in range(i + 1, len(route_static)):
                    if route_static[j].get("staCode") == end_station:
                        idx_a, idx_b = i, j
                        break
                break
        if idx_a < 0 or idx_b < 0:
            return None
        realtime = get_realtime_bus_data(route, direction)
        if not realtime or "routeInfo" not in realtime:
            return None
        return (route, direction, static, realtime)

    route_data_list: List[Tuple[str, str, dict, dict]] = []
    with ThreadPoolExecutor(max_workers=REALTIME_FETCH_WORKERS) as executor:
        futures = {executor.submit(_fetch_route_data, rd): rd for rd in routes_to_check}
        for future in as_completed(futures):
            res = future.result()
            if res:
                route_data_list.append(res)

    for route, direction, static, realtime in route_data_list:
        route_static = static.get("routeInfo", [])
        idx_a = idx_b = -1
        for i, st in enumerate(route_static):
            if st.get("staCode") == start_station:
                for j in range(i + 1, len(route_static)):
                    if route_static[j].get("staCode") == end_station:
                        idx_a, idx_b = i, j
                        break
                break
        if idx_a < 0 or idx_b < 0:
            continue

        route_info = realtime.get("routeInfo", [])
        sta_name_map = {s.get("staCode"): s.get("staName", "") for s in route_static} if route_static else {}

        if not sta_a_name:
            st_a = route_info[idx_a]
            st_b = route_info[idx_b]
            sta_a_name = st_a.get("staName") or sta_name_map.get(start_station, start_station)
            sta_b_name = st_b.get("staName") or sta_name_map.get(end_station, end_station)
            # 收集路段内所有站点（起点到终点）
            if not segment_stations:
                for k in range(idx_a, idx_b + 1):
                    s = route_info[k]
                    segment_stations.append({
                        "code": s.get("staCode", ""),
                        "name": s.get("staName") or sta_name_map.get(s.get("staCode", ""), ""),
                        "index": k - idx_a,
                    })

        # 分类车辆
        n = len(route_info)
        for i in range(len(route_info)):
            st = route_info[i]
            sta_code = st.get("staCode", "")
            sta_name = st.get("staName") or sta_name_map.get(sta_code, "")
            next_idx = (i + 1) % n
            next_st = route_info[next_idx]
            next_code = next_st.get("staCode", "")
            next_name = next_st.get("staName") or sta_name_map.get(next_code, "")

            for bus in st.get("busInfo", []):
                plate = bus.get("busPlate", "")
                if not plate:
                    continue
                status_val = str(bus.get("status", ""))
                is_at_stop = status_val == "1"  # 1=停靠在当前站，0=当前站到下一站之间行驶
                item = {
                    "route": route,
                    "busPlate": plate,
                    "positionBetween": [sta_code, next_code],
                    "positionBetweenNames": [sta_name, next_name],
                    "currentStationIndex": i + 1,
                    "nextStationIndex": next_idx + 1,
                    "isAtStation": is_at_stop,
                    "speed": bus.get("speed", ""),
                    "status": status_val,
                    "passengerFlow": bus.get("passengerFlow", ""),
                }
                if is_at_stop:
                    # status=1：停靠在当前站
                    if i < idx_a:
                        # 停靠在起点前方，属于「即将到达起点」
                        stops_to_start = idx_a - i
                        item["stopsToStart"] = stops_to_start
                        if route not in _approaching_by_route:
                            _approaching_by_route[route] = {
                                "route_info": route_info,
                                "idx_a": idx_a,
                                "sta_name_map": sta_name_map,
                                "buses": [],
                            }
                        _approaching_by_route[route]["buses"].append(item)
                        sp = None
                        try:
                            sp = float(bus.get("speed") or 0)
                        except (TypeError, ValueError):
                            sp = 0
                        eta = estimate_eta_minutes(stops_to_start, sp if sp else None)
                        if eta is not None:
                            item["etaToStartMinutes"] = eta
                        approaching_start.append(item)
                    elif i == idx_a:
                        # 停靠在起点站
                        item["stopsToStart"] = 0
                        if route not in _approaching_by_route:
                            _approaching_by_route[route] = {
                                "route_info": route_info,
                                "idx_a": idx_a,
                                "sta_name_map": sta_name_map,
                                "buses": [],
                            }
                        _approaching_by_route[route]["buses"].append(item)
                        item["etaToStartMinutes"] = 0.0
                        approaching_start.append(item)
                    elif idx_a < i <= idx_b:
                        # 停靠在 A-B 区间内某站（含终点）：
                        # 归入前一段 segment，前端据此显示在该站点左侧
                        item["stopsToEnd"] = idx_b - i
                        item["segmentIndex"] = max(0, i - idx_a - 1)
                        between_stations.append(item)
                    else:
                        # 已过终点
                        pass
                elif i < idx_a:
                    # status=0：在当前站与下一站之间行驶，且当前站仍在起点之前 → 即将到达起点
                    sp = None
                    try:
                        sp = float(bus.get("speed") or 0)
                    except (TypeError, ValueError):
                        sp = 0
                    stops_to_start = idx_a - i
                    item["stopsToStart"] = stops_to_start
                    if route not in _approaching_by_route:
                        _approaching_by_route[route] = {
                            "route_info": route_info,
                            "idx_a": idx_a,
                            "sta_name_map": sta_name_map,
                            "buses": [],
                        }
                    _approaching_by_route[route]["buses"].append(item)
                    eta = estimate_eta_minutes(stops_to_start, sp if sp else None)
                    if eta is not None:
                        item["etaToStartMinutes"] = eta
                    approaching_start.append(item)
                elif i == idx_a:
                    # status=0：在起点与下一站之间行驶，已离开起点，归入第 0 段
                    item["stopsToEnd"] = idx_b - i
                    item["segmentIndex"] = 0
                    sp = None
                    try:
                        sp = float(bus.get("speed") or 0)
                    except (TypeError, ValueError):
                        pass
                    eta = estimate_eta_minutes(item["stopsToEnd"], sp if sp else None)
                    if eta is not None:
                        item["etaToEndMinutes"] = eta
                    between_stations.append(item)
                elif i >= idx_b:
                    # status=0 且 i==idx_b：车在终点站与下一站之间，已驶离终点，不算在路段内
                    pass
                elif idx_a < i < idx_b:
                    # status=0：在 i 与 i+1 之间行驶，归入 segment(i-idx_a)
                    stops_to_end = idx_b - i
                    item["stopsToEnd"] = stops_to_end
                    item["segmentIndex"] = i - idx_a  # 在路段中的第几段（0=起点与第2站之间）
                    sp = None
                    try:
                        sp = float(bus.get("speed") or 0)
                    except (TypeError, ValueError):
                        pass
                    eta = estimate_eta_minutes(stops_to_end, sp if sp else None)
                    if eta is not None:
                        item["etaToEndMinutes"] = eta
                    between_stations.append(item)

    # 按路线分别构建「即将到达」的路线图（每条路线独立显示其站点序列）
    approaching_by_route: dict = {}
    for route, data in _approaching_by_route.items():
        route_info = data["route_info"]
        idx_a = data["idx_a"]
        name_map = data["sta_name_map"]
        buses = data["buses"]
        max_stops = max(b["stopsToStart"] for b in buses)
        start_idx = max(0, idx_a - max_stops)
        stations = []
        for k in range(start_idx, idx_a + 1):
            s = route_info[k]
            code = s.get("staCode", "")
            stations.append({
                "code": code,
                "name": s.get("staName") or name_map.get(code, ""),
                "stopsToStart": idx_a - k,
            })
        for b in buses:
            b["segmentIndex"] = max_stops - b["stopsToStart"]
        approaching_by_route[route] = {"stations": stations, "buses": buses}

    # 起点左侧仅展示「停靠在起点」的车辆，严格按 status=1（isAtStation=True）
    buses_at_start = [b for b in approaching_start if b.get("stopsToStart") == 0 and b.get("isAtStation")]

    return {
        "stationA": {"code": start_station, "name": sta_a_name or start_station},
        "stationB": {"code": end_station, "name": sta_b_name or end_station},
        "segmentStations": segment_stations,
        "approachingByRoute": approaching_by_route,
        "approachingStart": approaching_start,
        "betweenStations": between_stations,
        "busesAtStart": buses_at_start,
        "totalApproaching": len(approaching_start),
        "totalBetween": len(between_stations),
    }


def get_eta_for_section(
    route_name: str,
    start_station: Optional[str] = None,
    end_station: Optional[str] = None,
    direction: str = "0",
) -> dict:
    """
    获取某段线路中所有预计过来的车辆
    
    Args:
        route_name: 路线编号，如 9A
        start_station: 起始站代码（如 M23），None 表示从首站开始
        end_station: 终点站代码（如 M177），None 表示到末站
        direction: 方向 0 或 1
    
    Returns:
        包含该路段各站预计到达车辆信息的字典
    """
    static = get_route_static_data(route_name, direction)
    realtime = get_realtime_bus_data(route_name, direction)

    if not realtime or "routeInfo" not in realtime:
        return {"route": route_name, "error": "无法获取实时数据", "buses": []}

    route_info = realtime.get("routeInfo", [])
    static_info = (static or {}).get("routeInfo", []) if static else []
    # 合并站点名称（静态数据有，实时数据可能没有）
    sta_name_map = {s.get("staCode"): s.get("staName", "") for s in static_info} if static_info else {}

    # 确定路段范围
    start_idx = 0
    end_idx = len(route_info) - 1

    if start_station:
        for i, st in enumerate(route_info):
            if st.get("staCode") == start_station:
                start_idx = i
                break
    if end_station:
        for i, st in enumerate(route_info):
            if st.get("staCode") == end_station:
                end_idx = i
                break

    if start_idx > end_idx:
        start_idx, end_idx = end_idx, start_idx

    # 收集该路段内所有预计到达的车辆
    section_buses = []
    seen_plates = set()

    for i in range(start_idx, end_idx + 1):
        st = route_info[i]
        sta_code = st.get("staCode", "")
        sta_name = st.get("staName") or sta_name_map.get(sta_code, "")
        bus_list = st.get("busInfo", [])

        for bus in bus_list:
            plate = bus.get("busPlate", "")
            if plate and plate not in seen_plates:
                seen_plates.add(plate)
                section_buses.append({
                    "busPlate": plate,
                    "currentStation": sta_code,
                    "currentStationName": sta_name,
                    "stationIndex": i + 1,
                    "nextStationIndex": (i + 1) % len(route_info) + 1 if route_info else None,
                    "isAtStation": str(bus.get("status", "")) == "1",
                    "speed": bus.get("speed", ""),
                    "status": bus.get("status", ""),  # 1=到站/待发 0=行驶中
                    "passengerFlow": bus.get("passengerFlow", ""),
                    "isFacilities": bus.get("isFacilities", ""),
                })

    # 按站点顺序整理：每站有哪些车会到
    stations_eta = []
    for i in range(start_idx, end_idx + 1):
        st = route_info[i]
        buses_at_stop = st.get("busInfo", [])
        sc = st.get("staCode")
        stations_eta.append({
            "stationCode": sc,
            "stationName": st.get("staName") or sta_name_map.get(sc, ""),
            "stationIndex": i + 1,
            "incomingBuses": [
                {
                    "busPlate": b.get("busPlate"),
                    "speed": b.get("speed"),
                    "status": b.get("status"),
                    "isAtStation": str(b.get("status", "")) == "1",
                }
                for b in buses_at_stop
            ],
        })

    return {
        "route": route_name,
        "direction": direction,
        "startStation": route_info[start_idx].get("staCode") if route_info else None,
        "endStation": route_info[end_idx].get("staCode") if route_info else None,
        "totalIncomingBuses": len(section_buses),
        "buses": section_buses,
        "stationsETA": stations_eta,
        "lastBusPlate": realtime.get("lastBusPlate"),
    }


def _parse_routes_arg(arg: str) -> List[str]:
    """解析路线参数：all / 9A,1,3 / 9A 1 3"""
    arg = arg.strip().upper()
    if arg == "ALL":
        routes = get_all_routes()
        if not routes:
            # 回退到本地 bus_routes.json
            routes_file = Path(__file__).parent / "data" / "bus_routes.json"
            if routes_file.exists():
                with open(routes_file, encoding="utf-8") as f:
                    data = json.load(f)
                routes = [r.get("route_no", "") for r in data if r.get("route_no")]
        return routes
    # 支持逗号或空格分隔
    return [r.strip() for r in arg.replace(",", " ").split() if r.strip()]


def main():
    args = sys.argv[1:]
    route_arg = args[0] if args else "9A"
    start = args[1] if len(args) > 1 else None
    end = args[2] if len(args) > 2 else None
    direction = args[3] if len(args) > 3 else "0"

    # 常用路线预设：1–6 对应 COMMON_ROUTES
    if route_arg in ("1", "2", "3", "4", "5", "6"):
        idx = int(route_arg) - 1
        start, end = COMMON_ROUTES[idx][0], COMMON_ROUTES[idx][1]
        route_arg = "-s"  # 复用下面的站点模式逻辑

    # 仅站点模式：-s / --stations 起始站 结束站
    if route_arg in ("-s", "--stations") and start and end:
        print(f"正在查询：途经 {start} → {end} 的车辆（全路线）...")
        result = get_buses_by_stations_only(start, end)
        sta_a = result.get("stationA", {})
        sta_b = result.get("stationB", {})
        a_name = sta_a.get("name") or start
        b_name = sta_b.get("name") or end

        print(f"\n【即将到达 {a_name}({start}) 并会经过两站的车】共 {result['totalApproaching']} 辆：")
        for bus in result.get("approachingStart", []):
            prev_name, curr_name = bus.get("positionBetweenNames", ["?", "?"])
            pos_str = f"{prev_name} 与 {curr_name} 之间"
            speed_str = f" {bus.get('speed', '')}km/h" if bus.get("speed") else ""
            stops = bus.get("stopsToStart", "?")
            eta_str = f" 约 {bus['etaToStartMinutes']} 分钟到起点" if bus.get("etaToStartMinutes") is not None else ""
            print(f"  {bus['route']} {bus['busPlate']}: {pos_str}{speed_str}  （距起点 {stops} 站{eta_str}）")

        print(f"\n【当前在 {a_name} 与 {b_name} 之间运行的车】共 {result['totalBetween']} 辆：")
        for bus in result.get("betweenStations", []):
            prev_name, curr_name = bus.get("positionBetweenNames", ["?", "?"])
            pos_str = f"{prev_name} 与 {curr_name} 之间"
            speed_str = f" {bus.get('speed', '')}km/h" if bus.get("speed") else ""
            eta_str = f" 约 {bus['etaToEndMinutes']} 分钟到终点" if bus.get("etaToEndMinutes") is not None else ""
            stops_end = bus.get("stopsToEnd", "")
            stops_str = f" （距终点 {stops_end} 站{eta_str}）" if stops_end != "" else eta_str
            print(f"  {bus['route']} {bus['busPlate']}: {pos_str}{speed_str}{stops_str}")

        print("\n（仅查询，未生成文件）")
        return 0

    # 解析路线列表
    routes = _parse_routes_arg(route_arg)
    if not routes:
        print("未找到可用路线，请检查 bus_routes.json 或网络")
        return 1

    if len(routes) == 1:
        # 单路线模式
        route = routes[0]

        # A站到B站查询模式：必须同时指定 start 和 end
        if start and end:
            print(f"正在查询 {route} 路线：从 {start} 开往 {end} 的车辆...")
            result = get_buses_from_a_to_b(route, start, end)

            if "error" in result:
                print(result["error"])
                return 1

            sta_a = result.get("stationA", {})
            sta_b = result.get("stationB", {})
            a_name = sta_a.get("name") or sta_a.get("code", start)
            b_name = sta_b.get("name") or sta_b.get("code", end)

            print(f"\n共 {result['totalBuses']} 辆从 {a_name}({start}) 开往 {b_name}({end}) 的车：")
            for bus in result.get("buses", []):
                prev_name, curr_name = bus.get("positionBetweenNames", ["?", "?"])
                prev_code, curr_code = bus.get("positionBetween", ["", ""])
                pos_str = f"{prev_name}({prev_code}) 与 {curr_name}({curr_code}) 之间"
                speed_str = f" {bus.get('speed', '')}km/h" if bus.get("speed") else ""
                print(f"  车 {bus['busPlate']}: 在 {pos_str}{speed_str}")
        else:
            # 原路段查询模式
            print(f"正在获取 {route} 路线实时报站信息...")
            result = get_eta_for_section(route, start, end, direction)

            if "error" in result:
                print(result["error"])
                return 1

            print(f"\n路线 {route} 该路段预计到达车辆数: {result['totalIncomingBuses']}")
            print("\n各站预计到达车辆:")
            for s in result.get("stationsETA", []):
                buses = s.get("incomingBuses", [])
                if buses:
                    plates = [f"{b['busPlate']}({b.get('speed', '-')}km/h)" for b in buses]
                    sta_name = s.get("stationName") or s.get("stationCode", "")
                    print(f"  {s['stationCode']} {sta_name}: {', '.join(plates)}")

        print("\n（仅查询，未生成文件）")
    else:
        # 多路线 / 全部路线模式
        print(f"正在获取 {len(routes)} 条路线实时报站信息...")
        all_results = {}
        ok_count = 0
        for i, route in enumerate(routes):
            result = get_eta_for_section(route, start, end, direction)
            if "error" not in result and result.get("totalIncomingBuses", 0) > 0:
                ok_count += 1
                all_results[route] = result
                print(f"  [{i+1}/{len(routes)}] {route}: {result['totalIncomingBuses']} 辆")
            else:
                all_results[route] = result
            if i < len(routes) - 1:
                time.sleep(REQUEST_DELAY)

        print(f"\n完成: {ok_count}/{len(routes)} 条路线有车辆（仅查询，未生成文件）")

    print("\n用法: python fetch_realtime_eta.py <1|2|3|4|5|6>       # 常用路线预设")
    print("      python fetch_realtime_eta.py -s <起始站> <结束站>  # 仅输入两站，查全路线")
    print("      python fetch_realtime_eta.py <路线> <A站> <B站>     # 指定路线查 A→B")
    print("常用路线: 1=提督/高士德→海邊新街  2=昌明花園→海邊新街  3=海邊新街→殷皇子馬路(1)  4=海邊新街→殷皇子馬路(2)  5=提督/高士德→殷皇子馬路(1)  6=昌明花園→殷皇子馬路(2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
