"""기록된 막차 출발 + 추정 역간시간. 전체 열차의 최단경로가 아니다."""
from datetime import datetime, timedelta
from heapq import heappush, heappop
from itertools import count
from .subway import LINE_4, LINE_6, STATION_MINUTES, TRANSFER_MINUTES, MAX_TRANSFERS

ACCESS_MINUTES = 3
EGRESS_MINUTES = 3
SEARCH_HOURS = 6


def served_stops(event):
    """검증 가능한 선형 구간만 사용. 종착역을 넘어서 운행하지 않는다."""
    line, station, terminal = event["line"], event["station"], event["terminal"]
    if line == "4호선":
        stations = LINE_4
    elif line == "6호선":
        # 응암순환은 행별 회차/운행 경로 자료가 없어 안내만 제공한다.
        stations = [LINE_6[0]] + LINE_6[6:]
    else:
        return []  # 다른 기존 호선의 축약 목록을 실제 인접역으로 사용하지 않는다.
    if station not in stations or terminal not in stations or event["train_origin"] not in stations:
        return []
    a, b, source = stations.index(station), stations.index(terminal), stations.index(event["train_origin"])
    step = -1 if event["direction_code"] == "1" else 1
    if (b - a) * step <= 0 or (a - source) * step < 0:
        return []
    return [stations[i] for i in range(a, b + step, step)]


def search_routes(origin, departure, timetable):
    """각 탑승에서 모든 기록된 출발을 검토. 같은 상태의 이른 도착이 우선."""
    horizon = departure + timedelta(hours=SEARCH_HOURS)
    serial = count()
    queue = [(departure, next(serial), origin, None, [])]
    best = {}
    results = []
    while queue:
        arrival, _, station, previous_line, route = heappop(queue)
        state = (station, previous_line, len(route))
        if state in best and arrival > best[state]:
            continue
        walk = TRANSFER_MINUTES if route else ACCESS_MINUTES
        ready = arrival + timedelta(minutes=walk)
        if len(route) > MAX_TRANSFERS:
            continue
        for event in timetable.events(station, ready, horizon):
            if previous_line == event["line"]:
                continue
            stops = served_stops(event)
            boarding = datetime.fromisoformat(event["last_departure"])
            for i in range(1, len(stops)):
                alighting = boarding + timedelta(minutes=i * STATION_MINUTES)
                if alighting > horizon:
                    continue
                segment = {"mode": "SUBWAY", "line": event["line"], "from": station, "to": stops[i],
                           "stations": stops[:i + 1], "direction": event["direction"],
                           "terminal": event["terminal"], "platform_arrival": ready.isoformat(),
                           "boarding_at": boarding.isoformat(), "alighting_at": alighting.isoformat(),
                           "waiting_time": round((boarding - ready).total_seconds() / 60, 2),
                           "walking_time": walk, "ride_time": round(i * STATION_MINUTES, 2),
                           "last_train": event, "certainty": "ESTIMATED",
                           "departure_source": "CSV_RECORDED_LAST_TRAIN", "arrival_source": "ESTIMATED_2_3_MIN_PER_STOP"}
                candidate = route + [segment]
                key = (stops[i], event["line"], len(candidate))
                if key in best and best[key] <= alighting:
                    continue
                best[key] = alighting
                results.append({"station": stops[i], "arrival": alighting, "route": candidate})
                heappush(queue, (alighting, next(serial), stops[i], event["line"], candidate))
    return results
