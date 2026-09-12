"""역+호선 그래프와 MVP 경로 탐색. 시간표 검증은 추후 별도 연결한다."""
import heapq
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)
STATION_MINUTES = 2.3
TRANSFER_MINUTES = 5
MAX_TRANSFERS = 2


def normalize_station_name(name):
    name = name.strip()
    return name if not name or name.endswith("역") else name + "역"


LINE_4 = [normalize_station_name(s) for s in "진접 오남 별내별가람 불암산 상계 노원 창동 쌍문 수유 미아 미아사거리 길음 성신여대입구 한성대입구 혜화 동대문 동대문역사문화공원 충무로 명동 회현 서울역 숙대입구 삼각지 신용산 이촌 동작 총신대입구 사당 남태령 선바위 경마공원 대공원 과천 정부과천청사 인덕원 평촌 범계 금정 산본 수리산 대야미 반월 상록수 한대앞 중앙 고잔 초지 안산 신길온천 정왕 오이도".split()]
LINE_6 = [normalize_station_name(s) for s in "응암 역촌 불광 독바위 연신내 구산 새절 증산 디지털미디어시티 월드컵경기장 마포구청 망원 합정 상수 광흥창 대흥 공덕 효창공원앞 삼각지 녹사평 이태원 한강진 버티고개 약수 청구 신당 동묘앞 창신 보문 안암 고려대 월곡 상월곡 돌곶이 석계 태릉입구 화랑대 봉화산 신내".split()]
# FR_CODE와 내부 코드는 동일하지 않다. 확인되지 않은 내부 코드는 비워 둔다.
STATION_INFO = {
    (station, line): {"fr_code": str(code), "station_cd": None}
    for line, stations, codes in [
        ("4호선", LINE_4, [405, 406] + list(range(408, 457))),
        ("6호선", LINE_6, range(610, 649)),
    ] for station, code in zip(stations, codes)
}


def build_graph(lines):
    graph = {(s, line): [] for line, stations in lines.items() for s in stations}
    station_lines = defaultdict(list)
    for station, line in graph:
        station_lines[station].append(line)

    def connect(a, b, minutes, transfer=0, bidirectional=True):
        graph[a].append((b, minutes, transfer))
        if bidirectional:
            graph[b].append((a, minutes, transfer))

    for line, stations in lines.items():
        if line == "6호선":
            # 응암순환 단방향. 구산-새절 직접 연결은 없다.
            loop = stations[:6] + [stations[0]]
            for a, b in zip(loop, loop[1:]):
                connect((a, line), (b, line), STATION_MINUTES, bidirectional=False)
            ordinary = [stations[0]] + stations[6:]
        else:
            ordinary = stations
        for a, b in zip(ordinary, ordinary[1:]):
            connect((a, line), (b, line), STATION_MINUTES)
    for station, memberships in station_lines.items():
        for i, a in enumerate(memberships):
            for b in memberships[i + 1:]:
                connect((station, a), (station, b), TRANSFER_MINUTES, 1)
                logger.debug("[TRANSFER] %s %s <-> %s", station, a, b)
    logger.info("[GRAPH] nodes=%s edges=%s", len(graph), sum(map(len, graph.values())))
    return graph, dict(station_lines)


def path_to_segments(path):
    segments = []
    for a, b in zip(path, path[1:]):
        if a[1] != b[1]:
            continue
        if segments and segments[-1]["line"] == a[1] and segments[-1]["to"] == a[0]:
            segments[-1]["to"] = b[0]
        else:
            segments.append({"mode": "SUBWAY", "line": a[1], "from": a[0], "to": b[0]})
    return segments


class SubwayRouter:
    def __init__(self, lines):
        self.graph, self.station_lines = build_graph(lines)

    def routes_from(self, origin, least_transfer=False):
        """한 번에 모든 도달역 탐색. 환승 수별 상태로 2회 제한을 정확히 적용."""
        origin = normalize_station_name(origin)
        queue, best, results = [], {}, {}
        def priority(minutes, transfers, stops):
            return (transfers, minutes, stops) if least_transfer else (minutes, transfers, stops)
        for line in self.station_lines.get(origin, []):
            node = (origin, line)
            cost = priority(0, 0, 0)
            best[(node, 0)] = cost
            heapq.heappush(queue, (cost, node, 0, 0, 0, [node]))
        while queue:
            cost, node, minutes, transfers, stops, path = heapq.heappop(queue)
            if best.get((node, transfers)) != cost:
                continue
            if node[0] not in results:
                results[node[0]] = {"path": path, "route": path_to_segments(path),
                    "transfer_count": transfers, "station_count": stops,
                    "subway_time": round(stops * STATION_MINUTES, 1),
                    "transfer_time": transfers * TRANSFER_MINUTES,
                    "total_time": round(minutes, 1)}
            for nxt, duration, change in self.graph[node]:
                nt = transfers + change
                if nt > MAX_TRANSFERS:
                    continue
                nm, ns = round(minutes + duration, 1), stops + (1 - change)
                nc = priority(nm, nt, ns)
                state = (nxt, nt)
                if state not in best or nc < best[state]:
                    best[state] = nc
                    heapq.heappush(queue, (nc, nxt, nm, nt, ns, path + [nxt]))
        return results

    def find(self, origin, destination, least_transfer=False):
        result = self.routes_from(origin, least_transfer).get(normalize_station_name(destination))
        logger.debug("[ROUTE] %s -> %s [%s] %s", origin, destination,
                     "LEAST TRANSFER" if least_transfer else "FASTEST", result)
        return result
