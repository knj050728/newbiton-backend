"""CSV·시간 의존 탐색 회귀 테스트. 가상 연결편은 테스트 안에서만 사용."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from app import main
from app.timetable import Timetable, KST, service_time, day_code, display_time
from app.timed_routes import search_routes, served_stops


def event(station, line, start, terminal, boarding, direction="1"):
    return {"station": station, "line": line, "train_origin": start, "terminal": terminal,
            "direction_code": direction, "direction": direction, "last_departure": boarding,
            "last_display": boarding, "source": "TEST_FIXTURE"}


class Fixture:
    def __init__(self, events):
        self.items = events

    def events(self, station, ready, horizon):
        return [e for e in self.items if e["station"] == station
                and ready <= datetime.fromisoformat(e["last_departure"]) <= horizon]


class TimetableTests(unittest.TestCase):
    def request(self, **changes):
        args = dict(origin="사당역", destination="삼각지역", departure_date="2026-09-14",
                    departure_time="23:50", taxi_budget=20000)
        return main.recommend(main.RecommendationRequest(**(args | changes)))

    def test_csv_codes_and_coverage(self):
        t = Timetable()
        self.assertEqual(len(t.rows), 2077)
        self.assertEqual(t.invalid_rows, 0)
        row = next(r for r in t.rows if r["station"] == "도림천역")
        self.assertEqual(row["station_cd"], "0247")
        self.assertEqual(row["fr_code"], "234-1")
        self.assertEqual({r["day_code"] for r in t.rows}, {"1", "2", "3"})

    def test_midnight_245300(self):
        d = date(2026, 9, 14)
        value = service_time(d, "245300")
        self.assertEqual(value.isoformat(), "2026-09-15T00:53:00+09:00")
        self.assertEqual(display_time(value, d), "다음 날 00:53")

    def test_calendar_and_previous_service(self):
        self.assertEqual(day_code(date(2026, 9, 14)), "1")
        self.assertEqual(day_code(date(2026, 9, 12)), "2")
        self.assertEqual(day_code(date(2026, 9, 13)), "3")
        self.assertEqual(day_code(date(2026, 5, 5)), "3")
        self.assertEqual(day_code(date(2026, 8, 17)), "3")  # 대체공휴일
        n = Timetable().notices("강남", datetime(2026, 9, 15, 0, 20, tzinfo=KST))
        self.assertTrue(any(r.get("service_date") == "2026-09-14" and r["day_code"] == "1" for r in n))

    def test_actual_transit_and_components(self):
        result = self.request()
        p = next(p for p in result["all_plans"] if p["type"] == "TRANSIT")
        self.assertEqual(p["certainty"], "ESTIMATED")
        self.assertGreater(p["waiting_time"], 0)
        self.assertAlmostEqual(p["total_time"], sum(p[k] for k in
            ("access_time", "waiting_time", "subway_time", "transfer_time", "egress_time", "taxi_time")))
        self.assertEqual(p["route"][0]["boarding_at"], "2026-09-15T00:30:30+09:00")
        self.assertIsNone(result["last_subway_departure"])

    def test_transfer_missed_then_caught(self):
        start = datetime(2026, 9, 14, 23, 0, tzinfo=KST)
        first = event("사당역", "4호선", "오이도역", "삼각지역", "2026-09-14T23:05:00+09:00")
        missed = event("삼각지역", "6호선", "응암역", "한강진역", "2026-09-14T23:20:00+09:00", "2")
        r = search_routes("사당역", start, Fixture([first, missed]))
        self.assertFalse(any(c["station"] == "한강진역" for c in r))
        caught = missed | {"last_departure": "2026-09-14T23:22:00+09:00"}
        r = search_routes("사당역", start, Fixture([first, caught]))
        self.assertTrue(any(c["station"] == "한강진역" and len(c["route"]) == 2 for c in r))

    def test_later_train_and_short_turn(self):
        start = datetime(2026, 9, 14, 23, 0, tzinfo=KST)
        short = event("사당역", "4호선", "오이도역", "동작역", "2026-09-14T23:04:00+09:00")
        later = short | {"terminal": "서울역", "last_departure": "2026-09-14T23:08:00+09:00"}
        self.assertNotIn("서울역", served_stops(short))
        r = search_routes("사당역", start, Fixture([short, later]))
        seoul = [c for c in r if c["station"] == "서울역"]
        self.assertTrue(seoul)
        self.assertTrue(all(c["route"][0]["boarding_at"] == later["last_departure"] for c in seoul))

    def test_wait_changes_ranking(self):
        early = self.request()
        later = self.request(departure_date="2026-09-15", departure_time="00:26")
        self.assertEqual(early["fast"]["type"], "TAXI")
        self.assertEqual(later["fast"]["type"], "TRANSIT")
        self.assertLess(later["fast"]["waiting_time"], early["save"]["waiting_time"])

    def test_missing_and_error_not_ended(self):
        r = self.request(origin="알수없는역")
        self.assertEqual(r["transit_status"], "INSUFFICIENT_DATA")
        self.assertEqual(r["fast"]["type"], "TAXI")
        with patch.object(main.logger, "exception"), patch.object(main.TIMETABLE, "notices", side_effect=RuntimeError("provider failure")):
            r = self.request()
        self.assertEqual(r["transit_status"], "DATA_ERROR")
        self.assertEqual(r["fast"]["type"], "TAXI")
        with patch.object(main.logger, "exception"), patch.object(main, "_taxi", side_effect=RuntimeError("taxi provider failure")):
            r = self.request()
        self.assertEqual(r["fast"]["type"], "TRANSIT")

    def test_ended_taxi_and_same_recommendations(self):
        r = self.request(departure_date="2026-09-15", departure_time="02:00", taxi_budget=5000)
        self.assertEqual(r["transit_status"], "LISTED_SERVICE_ENDED")
        self.assertEqual(len(r["all_plans"]), 1)
        p = r["fast"]
        self.assertGreater(p["taxi_cost"], 0)
        self.assertEqual(p["taxi_cost"], p["total_cost"])
        self.assertEqual(p["last_train_notices"], [])
        self.assertEqual(r["save"], r["balance"])
        self.assertEqual(r["balance"], r["fast"])
        self.assertTrue(r["balance_over_budget"])

    def test_balance_budget(self):
        a = {"total_time": 50, "total_cost": 1650, "transfer_count": 1, "within_budget": True}
        b = {"total_time": 30, "total_cost": 8000, "transfer_count": 0, "within_budget": False}
        r = main.select_plans([a, b])
        self.assertIs(r["balance"], a)
        self.assertIs(r["fast"], b)


if __name__ == "__main__":
    unittest.main()
