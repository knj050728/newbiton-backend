"""실행: backend에서 python -m unittest -v test_routes"""
import contextlib
import io
import unittest
from app.main import (LINES, GRAPH, STATION_INFO, STATION_LINES, TRANSFER_STATIONS,
                      ROUTER, RecommendationRequest, recommend,
                      find_fastest_route, find_least_transfer_route)
from app.subway import SubwayRouter, normalize_station_name


class RouteTests(unittest.TestCase):
    def response(self, origin, destination, time="23:30", budget=20000):
        with contextlib.redirect_stdout(io.StringIO()):
            return recommend(RecommendationRequest(origin=origin, destination=destination,
                             departure_time=time, taxi_budget=budget))

    def test_1_gangnam_seoul(self):
        # 정적 그래프 검증. 시간표 연결 가능 여부는 test_timetable에서 별도 검증.
        route = find_least_transfer_route("강남역", "서울역")["route"]
        self.assertEqual([s["line"] for s in route], ["2호선", "4호선"])
        self.assertEqual(route[0]["to"], "사당역")

    def test_2_hapjeong_samgakji(self):
        r = find_fastest_route("합정역", "삼각지역")
        self.assertEqual(r["transfer_count"], 0)
        self.assertEqual(r["route"][0]["line"], "6호선")

    def test_3_sadang_samgakji(self):
        r = find_fastest_route("사당역", "삼각지역")
        self.assertEqual(r["transfer_count"], 0)
        self.assertEqual(r["route"][0]["line"], "4호선")

    def test_4_gangnam_samgakji(self):
        r = find_fastest_route("강남역", "삼각지역")
        self.assertEqual([s["line"] for s in r["route"]], ["2호선", "4호선"])

    def test_5_gangnam_gongdeok(self):
        r = find_fastest_route("강남역", "공덕역")
        self.assertIsNotNone(r)
        self.assertLessEqual(r["transfer_count"], 2)
        self.assertGreater(r["transfer_count"], 0)

    def test_6_two_objectives(self):
        for fn in (find_fastest_route, find_least_transfer_route):
            r = fn("합정역", "서울역")
            self.assertIsNotNone(r)
            print(fn.__name__, r)
        # 실제 노선 데이터와 분리한 알고리즘 단위 fixture: 두 목적함수 차이 검증.
        fixture = SubwayRouter({"A": ["출발역", "중간1역", "중간2역", "중간3역", "중간4역", "중간5역", "도착역"],
                                "B": ["출발역", "환승역"], "C": ["환승역", "도착역"]})
        self.assertEqual(fixture.find("출발역", "도착역")["transfer_count"], 1)
        self.assertEqual(fixture.find("출발역", "도착역", True)["transfer_count"], 0)

    def test_7_multi_source(self):
        self.assertEqual(set(STATION_LINES["사당역"]), {"2호선", "4호선"})
        for station, line in [("강남역", "2호선"), ("삼각지역", "4호선")]:
            r = find_fastest_route("사당", station)
            self.assertEqual(r["path"][0], ("사당역", line))
            self.assertEqual(r["transfer_count"], 0)
        self.assertEqual(normalize_station_name("서울역"), "서울역")

    def test_8_eungam_loop(self):
        router = SubwayRouter({"6호선": LINES["6호선"]})
        path = router.find("역촌역", "응암역")["path"]
        self.assertEqual([s for s, _ in path], ["역촌역", "불광역", "독바위역", "연신내역", "구산역", "응암역"])
        path = router.find("구산역", "새절역")["path"]
        self.assertEqual([s for s, _ in path], ["구산역", "응암역", "새절역"])
        self.assertEqual(len(LINES["4호선"]), 51)
        self.assertEqual(len(LINES["6호선"]), 39)
        self.assertEqual(STATION_INFO[("별내별가람역", "4호선")]["fr_code"], "408")

    def test_9_after_last_train(self):
        # 23:42 고정 기준은 폐기. CSV 운행분 종료 후를 검증한다.
        r = recommend(RecommendationRequest(origin="강남역", destination="홍대입구역",
                      departure_date="2026-09-15", departure_time="02:00", taxi_budget=5000))
        self.assertEqual(len(r["all_plans"]), 1)
        for key in ("save", "balance", "fast", "cheapest", "fastest", "least_transfer", "latest"):
            p = r[key]
            self.assertEqual(p["type"], "TAXI")
            self.assertGreater(p["taxi_cost"], 0)
            self.assertGreater(p["taxi_time"], 0)
            self.assertEqual(p["total_cost"], p["taxi_cost"])
            self.assertEqual(p["subway_cost"], 0)
            self.assertFalse(p["within_budget"])


if __name__ == "__main__":
    unittest.main()
