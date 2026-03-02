#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
澳门巴士实时报站 - Streamlit 应用
基于 Macau_bus_real_timev1.py 部署
"""

from datetime import timedelta
import streamlit as st
from Macau_bus_real_timev1 import (
    COMMON_ROUTES,
    get_buses_by_stations_only,
    get_buses_from_a_to_b,
    get_eta_for_section,
    get_all_routes,
)

# 缓存查询结果 10 秒，支持自动刷新
@st.cache_data(ttl=10)
def _cached_get_buses_by_stations(start: str, end: str) -> dict:
    return get_buses_by_stations_only(start, end)

@st.cache_data(ttl=10)
def _cached_get_buses_a_to_b(route: str, start: str, end: str) -> dict:
    return get_buses_from_a_to_b(route, start, end)

@st.cache_data(ttl=300)  # 路线列表 5 分钟缓存
def _cached_get_all_routes() -> list:
    return get_all_routes()

# 不同路线使用高对比度颜色，便于区分
ROUTE_COLORS = [
    "#dc2626", "#2563eb", "#16a34a", "#ea580c", "#7c3aed",
    "#0891b2", "#db2777", "#ca8a04", "#0d9488", "#6366f1",
    "#e11d48", "#0284c7", "#65a30d", "#c026d3", "#0f766e",
]
def _road_congestion_from_speed(speed_val) -> str:
    """根据车速推断所在道路拥挤程度：低速≈拥堵，正常≈顺畅"""
    if speed_val is None or speed_val == "":
        return "—"
    try:
        speed = float(speed_val)
    except (TypeError, ValueError):
        return "—"
    if speed < 10:
        return "拥堵"
    if speed < 20:
        return "一般"
    return "顺畅"


def _route_color(route: str) -> str:
    # 按字符位置加权求和，减少不同路线撞色
    h = sum((i + 1) * ord(c) for i, c in enumerate(str(route))) % len(ROUTE_COLORS)
    return ROUTE_COLORS[h]

st.set_page_config(
    page_title="澳门巴士实时报站",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义样式
st.markdown("""
<style>
    .stApp { max-width: 100%; padding: 0 1rem; }
    .bus-card {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
        color: white;
        padding: 1rem 1.25rem;
        border-radius: 12px;
        margin: 0.5rem 0;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    .bus-card .route { font-size: 1.1em; font-weight: bold; }
    .bus-card .eta { color: #7dd3fc; font-size: 0.95em; }
    .section-header { 
        font-size: 1.15em; 
        font-weight: 600; 
        margin: 1rem 0 0.5rem 0;
        padding-bottom: 0.3rem;
        border-bottom: 2px solid #2d5a87;
    }
    .route-map-wrapper { width: 100%; display: flex; justify-content: center; }
    .route-timeline { display: flex; flex-direction: column; align-items: center; gap: 0; margin: 1rem 0; width: 100%; max-width: 600px; }
    .route-timeline-align { display: flex; flex-direction: column; align-items: center; height: 420px; margin: 1rem auto; width: 100%; max-width: 600px; }
    .route-timeline-align .route-timeline-top { flex: 1 1 auto; display: flex; flex-direction: column; align-items: center; gap: 0; width: 100%; overflow-y: auto; min-height: 0; }
    .route-timeline-align > .route-stop.end { flex-shrink: 0; margin-top: auto; }
    .route-stop { 
        background: #e8f4fc; color: #1e3a5f; padding: 0.5rem 1rem; border-radius: 8px; 
        font-size: 0.95em; font-weight: 500; width: 100%; text-align: center; box-sizing: border-box;
    }
    .route-stop.start { background: #22c55e; color: white; }
    .route-stop.end { background: #1e3a5f; color: white; }
    .route-connector { color: #94a3b8; font-size: 1.1em; padding: 0.15rem 0; line-height: 1; }
    .route-segment { 
        display: flex; flex-direction: column; align-items: center; gap: 0.3rem; width: 100%; padding: 0.3rem 0;
    }
    .route-segment-buses { display: flex; flex-wrap: wrap; gap: 0.3rem; justify-content: center; }
    .bus-badge { 
        padding: 0.4rem 0.7rem; border-radius: 8px; font-weight: bold; color: white;
        display: inline-flex; flex-direction: column; align-items: center; gap: 0.1rem;
        border: 2px solid rgba(255,255,255,0.5); box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    }
    .bus-badge .route-num { font-size: 1.4em; font-weight: 800; }
    .bus-badge .plate-speed { font-size: 0.8em; opacity: 0.95; }
    .bus-badge.bus-near { background: #fef2f2 !important; color: #dc2626 !important; border-color: #dc2626 !important; box-shadow: 0 0 0 2px #dc2626; }
    .bus-badge.bus-near .route-num, .bus-badge.bus-near .plate-speed { color: #dc2626 !important; font-weight: 800; }
    /* 手机友好： approaching 卡片 */
    .approaching-card { padding: 1rem 1.25rem; border-radius: 12px; margin: 0.5rem 0; box-shadow: 0 2px 8px rgba(0,0,0,0.12); display: flex; align-items: center; gap: 1rem; min-height: 64px; color: white; }
    .approaching-card .card-route { font-size: 1.5em; font-weight: 800; min-width: 3rem; text-align: center; }
    .approaching-card .card-info { flex: 1; }
    .approaching-card .card-plate { font-size: 0.9em; opacity: 0.9; }
    .approaching-card .card-dist-eta { font-size: 1.1em; font-weight: 600; margin-top: 0.2rem; }
    .approaching-card.card-1stop { background: #fef2f2; border: 2px solid #dc2626; color: #dc2626; }
    .approaching-card.card-1stop .card-route, .approaching-card.card-1stop .card-dist-eta { color: #dc2626; font-weight: 800; }
    .approaching-card.card-2stop { background: #dcfce7; border: 2px solid #16a34a; color: #166534; }
    .approaching-card.card-2stop .card-route, .approaching-card.card-2stop .card-dist-eta { color: #166534; font-weight: 800; }
    .approaching-card.card-3stop { background: #fff7ed; border: 2px solid #ea580c; color: #c2410c; }
    .approaching-card.card-3stop .card-route, .approaching-card.card-3stop .card-dist-eta { color: #c2410c; font-weight: 800; }
    div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
    /* 并排路线列等高，起点底部对齐 */
    div[data-testid="stHorizontalBlock"] { align-items: stretch !important; }
    div[data-testid="stHorizontalBlock"] > div { display: flex !important; flex-direction: column !important; }
    div[data-testid="stHorizontalBlock"] .route-timeline-align { flex: 1 1 auto; }
</style>
""", unsafe_allow_html=True)

st.title("🚌 澳门巴士实时报站")
st.caption("数据来源：澳门 BIS 巴士报站系统")

# 侧边栏：查询模式
with st.sidebar:
    st.header("查询方式")
    query_mode = st.radio(
        "选择查询模式",
        ["常用路线预设", "站点查询（全路线）", "指定路线 A→B"],
        index=0,
    )

# 主区域
if query_mode == "常用路线预设":
    preset_labels = [
        f"1. {COMMON_ROUTES[0][2]} → {COMMON_ROUTES[0][3]}",
        f"2. {COMMON_ROUTES[1][2]} → {COMMON_ROUTES[1][3]}",
        f"3. {COMMON_ROUTES[2][2]} → {COMMON_ROUTES[2][3]}",
        f"4. {COMMON_ROUTES[3][2]} → {COMMON_ROUTES[3][3]}",
        f"5. {COMMON_ROUTES[4][2]} → {COMMON_ROUTES[4][3]}",
        f"6. {COMMON_ROUTES[5][2]} → {COMMON_ROUTES[5][3]}",
    ]
    preset_choice = st.selectbox("选择常用路线", preset_labels, index=0)
    idx = int(preset_choice[0]) - 1
    start_station, end_station = COMMON_ROUTES[idx][0], COMMON_ROUTES[idx][1]
    route_specified = None

elif query_mode == "站点查询（全路线）":
    col1, col2 = st.columns(2)
    with col1:
        start_station = st.text_input("起始站代码", value="M109", placeholder="如 M109")
    with col2:
        end_station = st.text_input("结束站代码", value="M127", placeholder="如 M127")
    route_specified = None

else:  # 指定路线 A→B
    routes = _cached_get_all_routes()
    if not routes:
        routes = ["9A", "1", "2", "3", "N2"]
    col1, col2, col3 = st.columns(3)
    with col1:
        route_specified = st.selectbox("路线", options=routes, index=0 if routes else 0)
    with col2:
        start_station = st.text_input("A 站代码", value="M23", placeholder="如 M23")
    with col3:
        end_station = st.text_input("B 站代码", value="M177", placeholder="如 M177")

# 初始化 session_state
if "query_params" not in st.session_state:
    st.session_state.query_params = None

col_btn1, col_btn2 = st.columns([1, 1])
with col_btn1:
    if st.button("🔍 查询", type="primary", use_container_width=True):
        if not start_station.strip() or not end_station.strip():
            st.error("请输入起始站和结束站代码")
        else:
            st.session_state.query_params = (
                start_station.strip(),
                end_station.strip(),
                route_specified,
            )
            st.rerun()
with col_btn2:
    if st.session_state.query_params and st.button("⏹ 停止自动刷新", use_container_width=True):
        st.session_state.query_params = None
        st.rerun()


@st.fragment(run_every=timedelta(seconds=10))
def _auto_refresh_results():
    """每 10 秒自动刷新结果"""
    params = st.session_state.get("query_params")
    if not params:
        return
    start_station, end_station, route_specified = params
    if not start_station or not end_station:
        return

    with st.spinner("正在查询，请稍候..."):
        if route_specified:
            # 指定路线模式
            result = _cached_get_buses_a_to_b(route_specified, start_station, end_station)
            if "error" in result:
                st.error(result["error"])
            else:
                sta_a = result.get("stationA", {})
                sta_b = result.get("stationB", {})
                a_name = sta_a.get("name") or sta_a.get("code", start_station)
                b_name = sta_b.get("name") or sta_b.get("code", end_station)
                st.success(f"共 {result['totalBuses']} 辆从 {a_name} 开往 {b_name} 的车")
                for bus in result.get("buses", []):
                    prev_name, curr_name = bus.get("positionBetweenNames", ["?", "?"])
                    pos_str = f"{prev_name} 与 {curr_name} 之间"
                    speed_str = f" {bus.get('speed', '')}km/h" if bus.get("speed") else ""
                    st.markdown(f"""
                    <div class="bus-card">
                        <span class="route">{bus['route']} {bus['busPlate']}</span><br>
                        <span>{pos_str}{speed_str}</span>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            # 站点模式（全路线）
            result = _cached_get_buses_by_stations(start_station, end_station)
            sta_a = result.get("stationA", {})
            sta_b = result.get("stationB", {})
            a_name = sta_a.get("name") or start_station
            b_name = sta_b.get("name") or end_station
            st.markdown(f'<p class="section-header">【即将到达 {a_name}({start_station}) 并会经过两站的车】</p>', unsafe_allow_html=True)
            approaching_by_route = result.get("approachingByRoute", {})
            bus_cards = []
            for route_name, route_data in approaching_by_route.items():
                buses = route_data.get("buses", []) or []
                for b in buses:
                    s = b.get("stopsToStart")
                    try:
                        stops = int(s) if s is not None else 999
                    except (TypeError, ValueError):
                        stops = 999
                    if stops in (1, 2, 3):
                        eta = b.get("etaToStartMinutes")
                        eta_val = float(eta) if eta is not None else 999.0
                        bus_cards.append((route_name, b, stops, eta_val))
            bus_cards.sort(key=lambda x: (x[2], x[3]))  # 按站数、ETA 排序（ETA 仅用于排序）

            if bus_cards:
                for route_name, bus, stops, _ in bus_cards:
                    card_class = f"approaching-card card-{stops}stop"
                    speed_txt = bus.get("speed", "")
                    speed_str = f"{speed_txt}km/h" if speed_txt else "—"
                    road_str = _road_congestion_from_speed(speed_txt)
                    html = f'''<div class="{card_class}">
                        <span class="card-route">{route_name}</span>
                        <div class="card-info">
                            <div class="card-plate">{bus.get("busPlate","")}</div>
                            <div class="card-dist-eta">距{stops}站 · {speed_str} · 道路{road_str}</div>
                        </div>
                    </div>'''
                    st.markdown(html, unsafe_allow_html=True)
                st.caption("仅显示距起点 1～3 站的车，按距离升序。红=1站/绿=2站/橙=3站。显示速度与道路拥挤程度（由车速推断），每 10 秒自动刷新")
            else:
                st.info("暂无距起点 1～3 站的车，请稍后再试")

            st.markdown(f'<p class="section-header">【当前在 {a_name} 与 {b_name} 之间运行的车】</p>', unsafe_allow_html=True)
            segment_stations = result.get("segmentStations", [])
            between_buses = result.get("betweenStations", [])

            if segment_stations:
                buses_by_segment = {}
                for bus in between_buses:
                    seg_idx = bus.get("segmentIndex", 0)
                    buses_by_segment.setdefault(seg_idx, []).append(bus)
                parts = []
                for i, stop in enumerate(segment_stations):
                    stop_class = "start" if i == 0 else ("end" if i == len(segment_stations) - 1 else "")
                    parts.append(f'<div class="route-stop {stop_class}">{stop["name"] or stop["code"]}</div>')
                    if i < len(segment_stations) - 1:
                        buses_in_seg = buses_by_segment.get(i, [])
                        bus_badges = "".join([
                            f'<span class="bus-badge" style="background:{_route_color(b["route"])}" title="{b.get("positionBetweenNames", ["",""])[0]} 与 {b.get("positionBetweenNames", ["",""])[1]} 之间">'
                            f'<span class="route-num">{b["route"]}</span>'
                            f'<span class="plate-speed">{b["busPlate"]}' + (f' {b.get("speed","")}km/h' if b.get("speed") else '') + '</span></span>'
                            for b in buses_in_seg
                        ])
                        parts.append(f'<div class="route-segment"><span class="route-connector">│</span><span class="route-segment-buses">{bus_badges}</span><span class="route-connector">│</span></div>')
                st.markdown(f'<div class="route-map-wrapper"><div class="route-timeline">{"".join(parts)}</div></div>', unsafe_allow_html=True)
                st.caption("路线站点及每段之间的车辆")

            if result["totalApproaching"] == 0 and result["totalBetween"] == 0:
                st.info("暂无车辆信息，请稍后再试或更换站点")


_auto_refresh_results()

st.sidebar.markdown("---")
st.sidebar.markdown("**常用路线**")
for i, r in enumerate(COMMON_ROUTES, 1):
    st.sidebar.markdown(f"{i}. {r[2]}→{r[3]}")
