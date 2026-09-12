# CSV 막차 연동

현재 구조: React/Vite `frontend/src/App.jsx`, FastAPI `backend/app/main.py`.
별도 Streamlit, recommend.py, taxi.py, 외부 열차 시간표/길찾기 API는 없다.
기존 정적 그래프(`subway.py`)는 남아 있지만 축약 노선 경로를 시간표 경로로 사용하지 않는다.

## 데이터 확인

`app/data/first_last.csv`는 사용자가 제공한 원본을 바이트 그대로 복사했다.
CP949, 2,077행, 16열. 코드와 시각은 문자열로 읽어 0247, 234-1 등을 보존한다.
호선별 행 수: 2=304, 3=258, 4=300, 5=327, 6=216, 7=312, 8=138, 9=222.
요일별: 1=693, 2=692, 3=692. 방향별: 1=1035, 2=1042.
막차 원시시각 범위: 232400~251130. 유효 시작일/종료일 및 갱신일 열은 없다.

열: 호선, 상/하행선, 요일, 전철역코드, 외부코드, 전철역명,
첫차시간, 첫차출발역코드, 첫차도착역코드, 첫차출발역명, 첫차도착역명,
막차시간, 막차출발역코드, 막차도착역코드, 막차출발역명, 막차도착역명.

[제공기관 코드 설명](https://www.data.go.kr/dataset/15003143/openapi.do):
요일 1=평일, 2=토요일, 3=일요일/공휴일. 방향 1=상행/내선, 2=하행/외선.
2호선 지선 구분을 임의 확정하지 않기 위해 화면에는 원시 방향 코드와 실제 행선지를 표시한다.
동일 역·호선·방향의 행도 삭제/집계하지 않고 종착역, 운행일, 원시 코드를 모두 유지한다.

## 구현 범위

- `timetable.py`: 파일 기준 경로 로딩, 일자별 막차 안내, 자정 넘김, 한국 공휴일 달력.
  245300=운행일 다음 날 00:53. 계산/응답은 +09:00 절대시각이다.
  새벽 04:00 전 조회는 전날 운행분도 표시한다. 전날/오늘 요일을 각자 계산한다.
  `holidays==0.104`의 KR 공휴일/대체공휴일 적용. 신규 임시공휴일은 달력 갱신 필요.
- `timed_routes.py`: CSV에 기록된 막차 출발 이벤트만 탐색한다. 중간 열차를 생성하지 않는다.
  모든 해당 이벤트와 종착역까지 각 하차역을 검토하고, 환승마다 승강장 도착 이후 이벤트를 찾는다.
  상태=(역, 이전 호선, 탑승 구간 수), 이른 도착 우선, 환승 최대 2회, 탐색 시간 범위 6시간.
  서로 다른 종착역/뒤 출발 이벤트도 검토한다. 동일 상태의 늦은 도착은 가지치기한다.
  4호선 전체 선형 구간, 6호선 응암~새절~신내 일반 구간만 사용한다.
  기점·종점·방향이 지지하는 운행 범위 밖은 제외한다.
  응암순환 회차/운행정보 및 다른 호선의 완전한 인접역 자료가 부족해 이들은 안내만 제공한다.
- 이동시간 가정: 첫 승강장까지 3분, 역간 2.3분, 환승 5분, 최종 하차 후 3분.
  대기는 준비 완료 시각~실제 CSV 막차 출발의 차이이며, 전체 열차 대기의 추정값이 아니다.
  열차 도착시각이 추정이므로 모든 지하철 경로는 ESTIMATED이고 환승 가능 확정이 아니다.
- 직행 택시를 항상 비교한다. 기존 역명 길이 기반 모의 시간/요금과 지하철 1650원은 유지했다.
  택시 API와 시간대별 할증은 미연결이다. HYBRID의 택시 탑승시각은 하차+이동 후로 반환한다.
  실제 요금 모델 연결 시 그 boarding_at을 전달해야 한다.
- SAVE=최저 총요금, BALANCE=택시 예산 안 가장 빠른 후보, FAST=예산 무관 가장 빠른 후보.
  도착시간 동률이면 환승 수 우선. 예산 내 후보가 없으면 BALANCE는 최저 비용과 예산초과 안내.

## 응답과 UI

`POST /api/recommend`: origin, destination, departure_time(HH:MM), departure_date(YYYY-MM-DD), taxi_budget.
날짜 생략 시 서버의 한국 날짜. `/api/last-trains` GET은 origin, departure_date, departure_time으로 조회.
기존 추천 키는 보존. `last_subway_departure`, 각 경로 `latest_departure`는 null:
전체 경로를 완주하는 최종 출발시각은 현재 자료로 확정할 수 없다.
`latest`는 하위호환을 위해 fastest를 반환하지만 가장 늦은 출발이라는 UI는 제공하지 않는다.

certainty: CSV_NOTICE / ESTIMATED / INSUFFICIENT_DATA.
확인된 전체 열차 기반 경로는 실제 연결편 시간표 확보 전까지 반환하지 않는다.
transit_status: ESTIMATED_CONNECTIONS / INSUFFICIENT_DATA / LISTED_SERVICE_ENDED / DATA_ERROR.
LISTED_SERVICE_ENDED는 해당 운행일의 조회된 행만 뜻하며 전체 운행 종료가 아니다.
comparison_scope=PARTIAL_ESTIMATED, search_horizon_hours=6.
노선·역 순서·승강장 도착·탑승·하차·대기·환승과 관련 막차 행은 백엔드가 반환한다.
택시 카드는 지하철 막차를 붙이지 않는다. 상단에는 출발역의 모든 CSV 호선 안내를 표시한다.

## 실행 및 검증

backend 폴더에서:
```
python -m pip install -r requirements.txt
python -m unittest -v test_routes test_timetable
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
frontend 폴더에서:
```
npm install
npm run dev
npm run build
```
프론트 http://localhost:5173, API 문서 http://localhost:8000/docs.
배포용 프론트 환경변수 VITE_API_BASE_URL, 백엔드 FRONTEND_ORIGIN.
CSV를 Python 번들에 포함하도록 vercel.json includeFiles를 설정했다. 실제 Vercel 배포는 미검증.

19개 회귀 테스트: 그래프, CP949/앞자리 코드, 24시 넘김, 평일/주말/공휴일/대체공휴일/전날 운행분,
CSV 기반 TRANSIT, 환승 놓침/성공, 중간 종착 및 뒤 열차, 대기에 따른 FAST 변경,
정보 누락, 조회 실패 주입, 막차 종료 택시 및 3개 추천 동일, 예산 처리.
조회 실패는 모의 장애 주입이며 실제 외부 API 호출 테스트가 아니다.

## 다음에 필요한 자료

1. 열차 식별자·운행일·방향·정차역 순서·각 역 도착/출발이 있는 전체 시간표
   (서울교통공사 역코드 기반 열차 시간표 등). 첫차·막차만으로 일중 최단시간을 계산할 수 없다.
2. 지원 호선의 정확한 인접역/분기/순환/회차 데이터와 역간 운행·정차시간.
   현재 다른 호선의 축약 목록은 실제 거리·시간 자료로 사용할 수 없다.
3. 역별 승강장 접근, 환승 연결, 출구 도보시간 및 목적지 좌표.
4. 실제 도로 경로·택시 요금 API. 현재 택시 수치는 모의값이다.

연결편이 확보되면 `Timetable.events()`를 전체 출발 이벤트 공급자로 교체하고
`served_stops()` 및 구간 도착시각을 실제 열차 정차표로 대체한다.
운행 실시간 보장 여부는 시간표 기반 경로와 별개로 관리해야 한다.
