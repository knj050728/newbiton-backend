"""첨부 CSV 원문을 보존해 조회. 막차 시각은 전체 시간표를 대신하지 않는다."""
import csv
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import holidays
from .subway import normalize_station_name

KST = timezone(timedelta(hours=9), "Asia/Seoul")
CSV_PATH = Path(__file__).resolve().parent / "data" / "first_last.csv"
DAY_LABELS = {"1": "평일", "2": "토요일", "3": "일요일·공휴일"}
CODE_SOURCE = "https://www.data.go.kr/dataset/15003143/openapi.do"
logger = logging.getLogger(__name__)


def day_code(service_date):
    # 대체공휴일 포함. 향후 지정되는 임시공휴일은 라이브러리 갱신 필요.
    calendar = holidays.country_holidays("KR", years=service_date.year)
    if service_date.weekday() == 6 or service_date in calendar:
        return "3"
    return "2" if service_date.weekday() == 5 else "1"


def service_time(service_date, value):
    """245300 -> 운행일+1일 00:53, 모든 코드/원시시간은 문자열 유지."""
    if len(value) != 6 or not value.isdigit():
        raise ValueError("잘못된 시간표 시각")
    h, m, s = int(value[:2]), int(value[2:4]), int(value[4:])
    if h > 47 or m > 59 or s > 59:
        raise ValueError("잘못된 시간표 시각")
    return datetime.combine(service_date, time(), KST) + timedelta(hours=h, minutes=m, seconds=s)


def display_time(value, service_date):
    prefix = "다음 날 " if value.date() > service_date else ""
    return prefix + value.strftime("%H:%M" + (":%S" if value.second else ""))


class Timetable:
    def __init__(self, path=CSV_PATH):
        self.rows = []
        self.index = defaultdict(list)
        self.station_lines = defaultdict(set)
        self.error = None
        self.invalid_rows = 0
        try:
            with Path(path).open(encoding="cp949", newline="") as handle:
                reader = csv.DictReader(handle)
                required = {"호선", "요일", "상/하행선", "전철역명", "전철역코드", "외부코드",
                            "막차시간", "막차출발역명", "막차도착역명", "막차도착역코드"}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError("CSV 필수 열 누락")
                for raw in reader:
                    try:
                        service_time(date(2026, 1, 1), raw["막차시간"])
                        station = normalize_station_name(raw["전철역명"])
                        line = str(int(raw["호선"].replace("호선", ""))) + "호선"
                        if raw["요일"] not in DAY_LABELS or raw["상/하행선"] not in ("1", "2"):
                            raise ValueError("코드 확인 불가")
                        row = {"station": station, "line": line, "day_code": raw["요일"],
                               "direction_code": raw["상/하행선"], "station_cd": raw["전철역코드"],
                               "fr_code": raw["외부코드"], "raw_last_time": raw["막차시간"],
                               "train_origin": normalize_station_name(raw["막차출발역명"]),
                               "terminal": normalize_station_name(raw["막차도착역명"]),
                               "terminal_cd": raw["막차도착역코드"], "raw": raw}
                        self.rows.append(row)
                        self.index[(station, line, row["day_code"])].append(row)
                        self.station_lines[station].add(line)
                    except (ValueError, TypeError, KeyError):
                        self.invalid_rows += 1
            if not self.rows:
                raise ValueError("유효한 CSV 행 없음")
        except (OSError, UnicodeError, ValueError, csv.Error):
            logger.exception("[TIMETABLE] CSV 읽기 실패")
            self.error = "CSV_READ_FAILED"

    def notices(self, station, departure, lines=None):
        station = normalize_station_name(station)
        result = []
        for line in sorted(set(lines or []) | self.station_lines.get(station, set())):
            found = False
            for service_date in (departure.date() - timedelta(days=1), departure.date()):
                # 전날 야간 운행 안내는 새벽 조회에만 노출한다(검색 시각은 절대시각 비교).
                if service_date < departure.date() and departure.hour >= 4:
                    continue
                for row in self.index.get((station, line, day_code(service_date)), []):
                    last = service_time(service_date, row["raw_last_time"])
                    # 전날 운행분은 오늘 자정을 넘는 행만 표시한다.
                    if service_date < departure.date() and last.date() < departure.date():
                        continue
                    found = True
                    direction = ("상행" if row["direction_code"] == "1" else "하행")
                    if line == "2호선":
                        # 지선/순환선 구분 없이 내외선을 임의 단정하지 않는다.
                        direction = "방향 코드 " + row["direction_code"]
                    result.append({k: v for k, v in row.items() if k != "raw"} | {
                        "direction": direction, "service_date": service_date.isoformat(),
                        "day_label": DAY_LABELS[row["day_code"]], "last_departure": last.isoformat(),
                        "last_display": display_time(last, service_date), "source": "CSV 시간표 기준",
                        "status": "LISTED_LAST_TRAIN_PASSED" if last < departure else "LISTED_LAST_TRAIN_AHEAD",
                        "certainty": "CSV_NOTICE"})
            if not found:
                result.append({"station": station, "line": line, "status": "MISSING_DATA",
                               "last_display": "막차 정보 확인 불가", "certainty": "INSUFFICIENT_DATA"})
        return result

    def events(self, station, departure, horizon):
        """기록된 막차만 반환한다. 배차간격이나 중간 열차를 생성하지 않는다."""
        return [r for r in self.notices(station, departure)
                if r.get("last_departure") and departure <= datetime.fromisoformat(r["last_departure"]) <= horizon]
