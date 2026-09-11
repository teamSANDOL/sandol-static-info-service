# sandol-static-info-service

한국공학대학교 관련 정적 정보를 제공하는 FastAPI 기반 API 서비스입니다.

## 개요

- 프레임워크: `FastAPI`
- Python 버전: `3.11` (`>=3.11,<3.12`)
- 주요 의존성: `uvicorn`, `httpx`, `python-dotenv`, `openai`, `apscheduler`
- API 기본 경로(root path): `/static-info`

## 프로젝트 구조

```text
.
├─ app/
│  ├─ config/          # 설정/로깅
│  ├─ routers/         # API 라우터
│  └─ utils/           # 외부 데이터 수집/가공 유틸
├─ main.py             # FastAPI 앱 엔트리포인트
├─ Dockerfile
└─ docker-compose.yml
```

## 환경 변수

`.env.example`을 복사해 `.env`를 만든 뒤 값을 설정하세요.

```bash
cp .env.example .env
```

- `DEBUG`: 로깅 레벨 제어 (`true`/`false`)
- `STATIC_INFO_API_KEY_SHA256`: 셔틀 시간표 관리 API(X-API-Key) 인증용 SHA-256 해시. 원본 키는 서버에 저장하지 않고 별도 보관하며, 루트 저장소의 `scripts/generate_secrets.py`로 생성한 해시만 여기에 넣습니다. 호출 시에는 원본 키를 `X-API-Key` 헤더로 보냅니다
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`, `OPENAI_SERVICE_TIER`, `OPENAI_TIMEOUT`: 셔틀 시간표 이미지 AI 파싱 (기본 `gpt-5.6-luna`, `high`, `flex`, 900초). flex tier는 저렴한 대신 느리고 혼잡 시 429가 나며 SDK가 3회 재시도. 이미지 1장에 약 2분(default tier 기준). 대안 `gpt-5-mini`+`high`는 정확도 동급이나 4배 느림. `gpt-5`는 분 단위 격자 표를 건너뛰었고 effort `medium`은 표 하나를 통째로 빠뜨린 적이 있음
- `DATA_DIR`: 파싱 결과(`shuttle_timetable.json`)와 원본 이미지 저장 위치 (기본 `./data`, gitignore)

## Docker 실행(추천)

```bash
docker compose up --build -d
```

- 기본 노출 주소: `http://localhost:8000`
- health: `http://localhost:8000/static-info/health`
- Swagger UI: `http://localhost:8000/static-info/docs`
- ReDoc: `http://localhost:8000/static-info/redoc`

중지:

```bash
docker compose down
```

## 로컬 실행

의존성 설치 후 `uvicorn`으로 실행합니다.

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 5600 --reload
```

- 로컬 기본 주소: `http://localhost:5600`
- health: `http://localhost:5600/health`
- OpenAPI 문서: `http://localhost:5600/docs`

## 셔틀 시간표 파이프라인

APScheduler cron으로 6시간마다(`app/jobs/scheduler.py`, `hour="*/6"`) iBook의 셔틀 이미지 목록을 확인합니다. 기동 시에도 1회 자동 동기화하므로 배포 직후 수동 호출이 필요 없습니다.

학기 중엔 이미지가 2장이지만 **방학엔 6장 등으로 늘어나고, 페이지마다(안내도/기간별 개별표/통합표) 운행 기간이 다릅니다.** 페이지 구성이 학기마다 유동적이라 **페이지 번호 → 노선 고정 매핑(`SHUTTLE_PLACES`)은 폐기**했습니다.

1. `Config.SHUTTLE_URL`에서 이미지 목록을 받아 각 이미지를 출처 페이지 번호 그대로 `place = f"{idx}페이지"`로 다룹니다 (몇 번째 표인지 사람이 구분하기 위한 용도일 뿐 노선을 뜻하지 않음)
2. place별로 이미지 다운로드 (`DATA_DIR/shuttle_images/<place>.jpg` 보관) 후 sha256 해시 계산. 기존 저장 문서와 해시가 같으면 파싱을 건너뜁니다 (OpenAI 호출 비용 절감이 핵심 가드)
3. 변경된 이미지만 OpenAI structured output(`app/schemas/shuttle.py`의 `ShuttleTimetable`)으로 시간표 추출. 동시에 3회 파싱해 (운행일, 방향, 출발시각, 운행 기간) 집합이 전부 일치하면 채택, 아니면 2회 더 돌려 나머지와 평균 유사도가 가장 높은 결과를 채택. 결과 문서의 `votes`에 실행 수/일치 여부/유사도 기록
4. `DATA_DIR/shuttle_timetable.json`에 `place` 키로 저장 (같은 place 재파싱 시 덮어씀). 문서에는 출처 페이지 번호 `page`와 이미지 상단 제목 원문 `title`도 함께 저장되어, 안내도인지 어느 기간의 표인지 사람이 구분할 수 있습니다. 한 이미지에 노선 여러 개가 있을 수 있어 노선 구분은 trip의 `route`로 합니다. 저장 문서에는 `image_sha256`, `source: "ai"`가 포함됩니다

trip 1건의 필드: `route`, `direction`, `origin`, `destination`, `via[]`, `day_type`, `day_note`, `departure_time`(HH:MM), `departure_time_end`(수시운행 범위), `valid_from`/`valid_to`(운행 기간 시작/종료, `MM-DD`), `period_note`(운행 기간/근무형태 원문, 예: `"정상근무 8.25~8.31"`), `boarding_place`, `note`. `direction`은 `app/config/shuttle_routes.py`의 6개 상수 중 하나이며 `route`/`origin`/`via`/`destination`은 그 표에서 채워집니다 (AI는 방향만 고름).

**운행 기간(`valid_from`/`valid_to`)**: 방학 중 이미지엔 기간이 표기되지만 **연도는 적혀 있지 않아** `MM-DD`만 저장합니다. 조회 시점에 연도를 붙여 비교하며, `valid_from > valid_to`(예: `12-20` ~ `02-28`)면 연말을 걸치는 구간으로 보고 `MM-DD >= valid_from or MM-DD <= valid_to`로 판정합니다. 기간 표기가 아예 없으면(학기 중 상시 시간표) 항상 유효한 것으로 취급합니다. 통합표(한 표 안에서 계절학기/단축근무/정상근무 라벨로 서로 다른 시각을 함께 표기)는 라벨별 기간이 각 trip에 반영되고, 같은 표기가 두 구간 이상이면(예: 정상근무가 학기 앞뒤로 두 번) trip이 구간 수만큼 나뉩니다.

**조회 시 중복 제거**: 방학 중엔 개별 기간표와 통합표가 같은 내용을 중복으로 담고 있을 수 있어, `GET /bus/timetable`은 (운행일, 방향, 출발 시각, 운행 기간) 기준으로 중복을 제거해 반환합니다(먼저 나온 place, 즉 페이지 번호가 앞선 쪽을 유지).

**사용자 직접 수정이 우선**: `PUT /bus/timetable/{place}`로 trips를 직접 입력하면 `source: "user"`로 저장되고, 이후 cron/refresh는 이 place를 AI로 다시 파싱하지 않고 건너뜁니다(로그만 남김). 되돌리려면 `DELETE /bus/timetable/{place}`로 사용자 수정본을 지우거나, `POST /bus/timetable/refresh?force=true`로 사용자 수정본과 해시 가드를 모두 무시하고 강제로 재파싱합니다.

자체 점검: `python tests/test_shuttle_timetable.py`

## 주요 API

`main.py`에서 `root_path=/static-info`를 사용하므로, compose 기준 모든 엔드포인트는 `/static-info` 하위로 접근합니다.

- `GET /static-info/health`
- `GET /static-info/bus/images`
- `GET /static-info/bus/image/{index}`
- `GET /static-info/bus/timetable` — AI로 파싱한 셔틀 시간표. 쿼리 `route`(부분 일치), `origin`/`destination`(정왕역·본교·제2캠퍼스, 경유지 포함), `day_type`(평일/토요일/일요일/주말/공휴일). 기본은 **오늘(KST) 기준 운행 기간에 해당하는 trip만** 반환하며, `date`(`YYYY-MM-DD`)로 다른 날짜를 조회하거나 `all=true`로 기간 필터 없이 전체를 조회할 수 있습니다. 방학 중 통합표/개별표 중복은 (운행일·방향·시각·운행 기간) 기준으로 자동 제거됩니다
- `POST /static-info/bus/timetable/refresh` — iBook 현재 이미지 전부를 백그라운드에서 동기화(변경분만 재파싱), 즉시 202 반환 (헤더 `X-API-Key: <발급받은 원본 키>`). 쿼리 `force=true`면 사용자 수정본과 해시 가드를 모두 무시하고 강제 재파싱. 기동 시 자동 동기화되므로 최초 적재용 수동 호출은 불필요, 결과는 잠시 후 `GET /bus/timetable`로 확인
- `PUT /static-info/bus/timetable/{place}` — place의 시간표를 사용자가 직접 입력해 저장 (헤더 `X-API-Key: <발급받은 원본 키>`, body `{"trips": [...]}`). 이후 cron/refresh가 덮어쓰지 않음
- `DELETE /static-info/bus/timetable/{place}` — 사용자 수정본을 삭제해 다시 AI 파싱 대상으로 되돌림 (헤더 `X-API-Key: <발급받은 원본 키>`)
- `GET /static-info/organization/tree`
- `GET /static-info/organization/search/{name}`
- `GET /static-info/organization/{path}/children`
- `GET /static-info/organization/{path}`
