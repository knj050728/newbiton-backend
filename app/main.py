import os
import logging
from .subway import (LINE_4, LINE_6, STATION_INFO, STATION_MINUTES,
                     TRANSFER_MINUTES, MAX_TRANSFERS, SubwayRouter, normalize_station_name)
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 프론트엔드 통신 허용 (CORS 설정)
origins = [
    "http://localhost:5173",
    os.getenv("FRONTEND_ORIGIN", ""),
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in origins if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

# 해커톤 MVP용 데이터. 실제 서울시 API 연결 시 이 계층만 교체하면 됩니다.
LINES = {
    "2호선": ["홍대입구역", "합정역", "당산역", "신도림역", "대림역", "사당역", "교대역", "강남역", "선릉역", "잠실역", "건대입구역", "왕십리역", "을지로3가역", "시청역"],
    "3호선": ["대화역", "고속터미널역", "교대역", "을지로3가역", "충무로역", "옥수역"],
    "4호선": ["사당역", "동대문역사문화공원역", "충무로역", "서울역"],
    "5호선": ["김포공항역", "영등포구청역", "을지로4가역", "왕십리역", "동대문역사문화공원역"],
    "7호선": ["대림역", "건대입구역", "고속터미널역"],
    "9호선": ["김포공항역", "당산역", "여의도역", "종합운동장역"],
}
LINES.update({"4호선": LINE_4, "6호선": LINE_6})
ROUTER = SubwayRouter(LINES)
GRAPH = ROUTER.graph
STATION_LINES = ROUTER.station_lines
TRANSFER_STATIONS = {s for s, lines in STATION_LINES.items() if len(lines) > 1}
logger = logging.getLogger(__name__)


def find_fastest_route(origin, destination):
    return ROUTER.find(origin, destination)


def find_least_transfer_route(origin, destination):
    return ROUTER.find(origin, destination, least_transfer=True)

from datetime import date, datetime, time, timedelta
from .timetable import Timetable, KST
from .timed_routes import search_routes, EGRESS_MINUTES, SEARCH_HOURS

TIMETABLE = Timetable()


class RecommendationRequest(BaseModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    departure_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    departure_date: date = Field(default_factory=lambda: datetime.now(KST).date())
    taxi_budget: int = Field(default=20000, ge=0)


def _taxi(origin: str, destination: str, base: int = 18) -> tuple[int, int]:
    # 장소 좌표 API가 없거나 실패해도 추천을 계속하도록 보수적인 추정값 사용
    distance = abs(len(origin) - len(destination)) + 4
    taxi_time, taxi_cost = base + distance * 2, 4800 + distance * 550
    logger.debug("[TAXI CALC] %s -> %s time=%s cost=%s", origin, destination, taxi_time, taxi_cost)
    return taxi_time, taxi_cost

def _make_taxi_plan(origin: str, destination: str, departure_time: str, budget: int) -> dict[str, Any]:
    """직행 택시 후보의 단일 생성 지점."""
    taxi_time, taxi_cost = _taxi(origin, destination)
    return {
        "type": "TAXI", "route": [{"mode": "TAXI", "from": origin, "to": destination}],
        "station": None, "transfer_count": 0, "subway_time": 0, "waiting_time": 0,
        "transfer_time": 0, "taxi_time": taxi_time, "total_time": taxi_time,
        "subway_cost": 0, "taxi_cost": taxi_cost, "total_cost": taxi_cost,
        "latest_departure": departure_time, "within_budget": taxi_cost <= budget,
    }


def select_plans(plans):
    """SAVE=최저 비용, BALANCE=예산 안 최단시간(없으면 최저비용), FAST=최단시간."""
    if not plans:
        return {k: None for k in ("save", "balance", "fast", "cheapest", "fastest", "least_transfer", "latest")}
    cheapest = min(plans, key=lambda p: (p["total_cost"], p["total_time"], p["transfer_count"]))
    speed = lambda p: (p["total_time"], p["transfer_count"], p["total_cost"])
    fastest = min(plans, key=speed)
    within = [p for p in plans if p["within_budget"]]
    balance = min(within, key=speed) if within else cheapest
    least = min(plans, key=lambda p: (p["transfer_count"], p["total_time"], p["total_cost"]))
    # 출발 가능 최종시각은 전체 시간표 없이는 계산할 수 없다.
    return {"save": cheapest, "balance": balance, "fast": fastest, "cheapest": cheapest,
            "fastest": fastest, "least_transfer": least, "latest": fastest}


def make_transit_plan(candidate, destination, departure, budget):
    route = [dict(segment) for segment in candidate["route"]]
    is_taxi = candidate["station"] != destination
    exit_at = candidate["arrival"] + timedelta(minutes=EGRESS_MINUTES)
    taxi_time, taxi_cost = _taxi(candidate["station"], destination) if is_taxi else (0, 0)
    arrival = exit_at + timedelta(minutes=taxi_time)
    if is_taxi:
        route.append({"mode": "TAXI", "from": candidate["station"], "to": destination,
                      "boarding_at": exit_at.isoformat(), "alighting_at": arrival.isoformat(),
                      "certainty": "ESTIMATED"})
    subways = candidate["route"]
    return {"type": "HYBRID" if is_taxi else "TRANSIT", "route": route,
            "station": candidate["station"], "transfer_count": len(subways) - 1,
            "subway_time": round(sum(s["ride_time"] for s in subways), 2),
            "waiting_time": round(sum(s["waiting_time"] for s in subways), 2),
            "transfer_time": sum(s["walking_time"] for s in subways[1:]),
            "access_time": subways[0]["walking_time"], "egress_time": EGRESS_MINUTES,
            "taxi_time": taxi_time, "total_time": round((arrival - departure).total_seconds() / 60, 2),
            "subway_cost": 1650, "taxi_cost": taxi_cost, "total_cost": 1650 + taxi_cost,
            "latest_departure": None, "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(),
            "within_budget": taxi_cost <= budget, "certainty": "ESTIMATED",
            "certainty_label": "추정 경로 · CSV에 기록된 막차만 검토",
            "assumptions": ["역간 2.3분", "첫 승강장 이동 3분", "환승 이동 5분", "하차 후 이동 3분",
                            "택시 시간·요금과 지하철 요금은 기존 MVP 추정치"],
            "last_train_notices": [s["last_train"] for s in subways]}


@app.get("/api/last-trains")
def last_trains(origin: str, departure_date: date, departure_time: time):
    departure = datetime.combine(departure_date, departure_time, KST)
    return {"source": "CSV 시간표 기준", "status": "DATA_ERROR" if TIMETABLE.error else "CSV_NOTICE",
            "items": TIMETABLE.notices(origin, departure, STATION_LINES.get(normalize_station_name(origin), []))}


@app.post("/api/recommend")
def recommend(req: RecommendationRequest):
    departure = datetime.combine(req.departure_date, time.fromisoformat(req.departure_time), KST)
    origin, destination = normalize_station_name(req.origin), normalize_station_name(req.destination)
    if destination not in STATION_LINES and destination not in TIMETABLE.station_lines:
        destination = req.destination.strip()
    plans, warnings, notices = [], [], []
    transit_status = "INSUFFICIENT_DATA"
    try:
        # 지하철 정보 실패와 무관하게 택시 직행을 항상 비교한다.
        taxi = _make_taxi_plan(req.origin, req.destination, req.departure_time, req.taxi_budget)
        taxi.update({"latest_departure": None, "departure_at": departure.isoformat(),
                     "arrival_at": (departure + timedelta(minutes=taxi["taxi_time"])).isoformat(),
                     "certainty": "ESTIMATED", "certainty_label": "추정 경로 · 택시 MVP 계산",
                     "assumptions": ["거리·시간 API 미연결, 역명 길이 기반 기존 모의 요금"],
                     "access_time": 0, "egress_time": 0, "last_train_notices": []})
        taxi["route"][0].update({"boarding_at": taxi["departure_at"], "alighting_at": taxi["arrival_at"]})
        plans.append(taxi)
    except Exception:
        logger.exception("[TAXI] 조회 실패")
        warnings.append("택시 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.")
    try:
        if TIMETABLE.error:
            raise RuntimeError(TIMETABLE.error)
        notices = TIMETABLE.notices(origin, departure, STATION_LINES.get(origin, []))
        candidates = search_routes(origin, departure, TIMETABLE)
        for candidate in candidates:
            try:
                plans.append(make_transit_plan(candidate, destination, departure, req.taxi_budget))
            except Exception:
                logger.exception("[HYBRID] 택시 구간 조회 실패")
        if candidates:
            transit_status = "ESTIMATED_CONNECTIONS"
        # 종료는 CSV의 해당 운행분에 대해서만 표현한다. 전체 운행 종료로 일반화하지 않는다.
        relevant = [n for n in notices if n.get("last_departure")]
        service_day = departure.date() - timedelta(days=1) if departure.hour < 4 else departure.date()
        current = [n for n in relevant if n["service_date"] == service_day.isoformat()]
        if not candidates and current and all(datetime.fromisoformat(n["last_departure"]) < departure for n in current):
            transit_status = "LISTED_SERVICE_ENDED"
        if not notices:
            notices = [{"station": origin, "line": "확인 불가", "last_display": "막차 정보 확인 불가",
                        "status": "MISSING_DATA", "certainty": "INSUFFICIENT_DATA"}]
    except Exception:
        logger.exception("[TIMETABLE] 지하철 조회 실패")
        transit_status = "DATA_ERROR"
        warnings.append("지하철 시간표를 불러오지 못했어요. 조회된 택시 대안을 표시합니다.")
    messages = {
        "INSUFFICIENT_DATA": "막차 안내는 확인할 수 있지만 연결편을 판단할 자료가 부족해요. 운행 종료를 뜻하지는 않습니다.",
        "ESTIMATED_CONNECTIONS": "CSV에 기록된 막차와 추정 이동시간으로 계산한 대안입니다. 실제 환승 가능 여부는 확인이 필요해요.",
        "LISTED_SERVICE_ENDED": "해당 운행일의 조회된 막차 시각이 지났어요. 전체 노선의 운행 종료를 뜻하지는 않습니다.",
        "DATA_ERROR": "지하철 정보를 확인하지 못했어요. 전체 교통수단을 비교한 결과가 아닙니다.",
    }
    warnings.extend([messages[transit_status],
                     "중간 열차 시간표가 없어 전체 교통수단의 정확한 최단 경로는 계산하지 않습니다.",
                     "4·6호선 일반구간만 연결 검토합니다. 다른 호선·응암순환은 막차 안내만 제공합니다.",
                     "CSV 기준일 미기재: 최신 운행표와 다를 수 있습니다. 공휴일은 설치된 한국 공휴일 달력 기준입니다."])
    selected = select_plans(plans)
    if selected["balance"] and not selected["balance"]["within_budget"]:
        warnings.append("택시 예산 이내 후보가 없어 BALANCE에 최저 비용 대안을 표시했어요.")
    return selected | {"last_subway_departure": None, "all_plans": plans, "last_train_notices": notices,
                       "transit_status": transit_status, "warnings": warnings,
                       "departure_at": departure.isoformat(), "comparison_scope": "PARTIAL_ESTIMATED",
                       "balance_over_budget": bool(selected["balance"] and not selected["balance"]["within_budget"]),
                       "search_horizon_hours": SEARCH_HOURS, "source": "CSV 시간표 기준"}
