#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
澳门巴士实时报站 - Streamlit 应用
基于 Macau_bus_real_timev1.py 部署
"""

import streamlit as st
from Macau_bus_real_timev1 import (
    COMMON_ROUTES,
    get_buses_by_stations_only,
    get_buses_from_a_to_b,
    get_eta_for_section,
    get_all_routes,
)

st.set_page_config(
    page_title="澳门巴士实时报站",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义样式
st.markdown("""
<style>
    .stApp { max-width: 1200px; margin: 0 auto; }
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
    div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
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
        f"1. M109 → M127",
        f"2. M111 → M127",
        f"3. M127 → M170/1",
        f"4. M127 → M170/2",
    ]
    preset_choice = st.selectbox("选择常用路线", preset_labels, index=0)
    idx = int(preset_choice[0]) - 1
    start_station, end_station = COMMON_ROUTES[idx]
    route_specified = None

elif query_mode == "站点查询（全路线）":
    col1, col2 = st.columns(2)
    with col1:
        start_station = st.text_input("起始站代码", value="M109", placeholder="如 M109")
    with col2:
        end_station = st.text_input("结束站代码", value="M127", placeholder="如 M127")
    route_specified = None

else:  # 指定路线 A→B
    routes = get_all_routes()
    if not routes:
        routes = ["9A", "1", "2", "3", "N2"]
    col1, col2, col3 = st.columns(3)
    with col1:
        route_specified = st.selectbox("路线", options=routes, index=0 if routes else 0)
    with col2:
        start_station = st.text_input("A 站代码", value="M23", placeholder="如 M23")
    with col3:
        end_station = st.text_input("B 站代码", value="M177", placeholder="如 M177")

if st.button("🔍 查询", type="primary", use_container_width=True):
    if not start_station.strip() or not end_station.strip():
        st.error("请输入起始站和结束站代码")
    else:
        with st.spinner("正在查询，请稍候..."):
            if route_specified:
                # 指定路线模式
                result = get_buses_from_a_to_b(route_specified, start_station.strip(), end_station.strip())
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
                result = get_buses_by_stations_only(start_station.strip(), end_station.strip())
                sta_a = result.get("stationA", {})
                sta_b = result.get("stationB", {})
                a_name = sta_a.get("name") or start_station
                b_name = sta_b.get("name") or end_station

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("即将到达起点", f"{result['totalApproaching']} 辆")
                with col2:
                    st.metric("两站之间运行", f"{result['totalBetween']} 辆")

                st.markdown(f'<p class="section-header">【即将到达 {a_name}({start_station}) 并会经过两站的车】</p>', unsafe_allow_html=True)
                for bus in result.get("approachingStart", []):
                    prev_name, curr_name = bus.get("positionBetweenNames", ["?", "?"])
                    pos_str = f"{prev_name} 与 {curr_name} 之间"
                    speed_str = f" {bus.get('speed', '')}km/h" if bus.get("speed") else ""
                    stops = bus.get("stopsToStart", "?")
                    eta = bus.get("etaToStartMinutes")
                    eta_str = f" 约 {eta} 分钟到起点" if eta is not None else ""
                    st.markdown(f"""
                    <div class="bus-card">
                        <span class="route">{bus['route']} {bus['busPlate']}</span><br>
                        <span>{pos_str}{speed_str}</span>
                        <span class="eta">（距起点 {stops} 站{eta_str}）</span>
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown(f'<p class="section-header">【当前在 {a_name} 与 {b_name} 之间运行的车】</p>', unsafe_allow_html=True)
                for bus in result.get("betweenStations", []):
                    prev_name, curr_name = bus.get("positionBetweenNames", ["?", "?"])
                    pos_str = f"{prev_name} 与 {curr_name} 之间"
                    speed_str = f" {bus.get('speed', '')}km/h" if bus.get("speed") else ""
                    stops_end = bus.get("stopsToEnd", "")
                    eta = bus.get("etaToEndMinutes")
                    eta_str = f" 约 {eta} 分钟到终点" if eta is not None else ""
                    stops_str = f"（距终点 {stops_end} 站{eta_str}）" if stops_end != "" else eta_str
                    st.markdown(f"""
                    <div class="bus-card">
                        <span class="route">{bus['route']} {bus['busPlate']}</span><br>
                        <span>{pos_str}{speed_str}</span>
                        <span class="eta">{stops_str}</span>
                    </div>
                    """, unsafe_allow_html=True)

                if result["totalApproaching"] == 0 and result["totalBetween"] == 0:
                    st.info("暂无车辆信息，请稍后再试或更换站点")

st.sidebar.markdown("---")
st.sidebar.markdown("**常用路线**")
st.sidebar.markdown("1. M109→M127  ")
st.sidebar.markdown("2. M111→M127  ")
st.sidebar.markdown("3. M127→M170/1")
st.sidebar.markdown("4. M127→M170/2")
