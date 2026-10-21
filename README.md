# sandol-static-info-service

한국공학대학교 관련 정적 정보를 제공하는 FastAPI 기반 API 서비스입니다.

## 개요

- 프레임워크: `FastAPI`
- Python 버전: `3.11` (`>=3.11,<3.12`)
- 주요 의존성: `uvicorn`, `httpx`, `python-dotenv`, `pdfplumber`, `apscheduler`
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
- `DATA_DIR`: 파싱 결과(`shuttle_timetable.json`)와 원본 PDF 보관 위치 (기본 `./data`, gitignore)
- 그 밖의 값(Keycloak, 관리자 화면, TUK World, `SCHOOL_INFO_PATH`)은 `.env.example`의 주석을 참고하세요.

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

APScheduler cron으로 6시간마다(`app/jobs/scheduler.py`, `hour="*/6"`) iBook의 셔틀 원본 PDF를 확인합니다. 기동 시에도 1회 자동 동기화하므로 배포 직후 수동 호출이 필요 없습니다.

iBook 뷰어가 보여주는 이미지 대신 **원본 PDF**(`/web/RawFileList`)를 받아 좌표 기반으로 파싱합니다. 학교가 올리는 파일은 전부 한글(HWP)에서 export된 텍스트 PDF라 OCR이나 AI가 필요 없습니다. 외부 AI 호출은 쓰지 않습니다.

1. `Config.SHUTTLE_URL`의 bookcode로 `/web/RawFileList`를 조회해 원본 PDF URL을 얻습니다 (`app/utils/ibookdownloader.py`). `place`는 파일명에서 확장자를 뗀 값입니다
2. PDF 다운로드(`DATA_DIR/shuttle_pdf/<place>.pdf` 보관) 후 sha256 계산. 기존 저장 문서와 해시가 같으면 파싱을 건너뜁니다
3. `app/utils/shuttle_pdf.py`가 `pdfplumber`의 단어 좌표로 표를 읽습니다. 자세한 알고리즘은 그 모듈 docstring 참고
4. `DATA_DIR/shuttle_timetable.json`에 `place` 키로 저장. 저장 문서에는 `title`(문서 상단 제목 원문), `pdf_url`, `pdf_sha256`, `source: "pdf"`가 포함됩니다. 한 PDF에 노선 여러 개가 있으므로 노선 구분은 trip의 `route`로 합니다

**파서가 읽는 레이아웃**: (A) 『정왕역 ↔ 본교』 시-분 격자표 — 분 셀에 세로 괘선이 없어 `extract_tables`로는 안 읽히므로, `'시간'` 머리글 좌우의 세로 괘선으로 시 열을 잡고 좌측=하교/우측=등교로 가릅니다. (B) 『제2캠퍼스 ↔ 본교 ↔ 정왕역』 표 — `HH:MM` 리터럴이라 열 머리글에서 방향만 정합니다. (C) `'<장소> 출발'` 열 머리글을 쓰는 2캠 전용 문서.

**검증**: 2024년 이후 iBook 등록분 PDF 69건 전수 기준으로, 원문의 모든 `HH:MM` 리터럴과 격자표 분 셀 개수가 결과와 일치함을 확인했습니다. 방향을 6개 상수 중 하나로 확정하지 못한 건(표 머리글이 괘선에 잘린 소수 문서)은 버리고 개수만 `WARNING` 로그로 남깁니다.

trip 1건의 필드: `route`, `direction`, `origin`, `destination`, `via[]`, `day_type`, `day_note`, `departure_time`(HH:MM), `departure_time_end`(수시운행 범위), `valid_from`/`valid_to`(운행 기간 시작/종료, `MM-DD`), `period_note`(운행 기간/근무형태 원문, 예: `"정상근무 8.25~8.31"`), `boarding_place`, `note`, `kind`(`fixed` 정시 1편 / `frequent` 수시운행 구간 / `arrival_board` 정시 출발 대신 도착버스에 탑승하는 구간, 종료=막차. 생략하면 종료 시각이 있으면 `frequent`, 없으면 `fixed`). `direction`은 `app/config/shuttle_routes.py`의 6개 상수 중 하나이며 `route`/`origin`/`via`/`destination`은 그 표에서 채워집니다 (파서는 방향만 확정).

**운행 기간(`valid_from`/`valid_to`)**: 방학 중 문서엔 기간이 표기되지만 **연도는 적혀 있지 않아** `MM-DD`만 저장합니다. 조회 시점에 연도를 붙여 비교하며, `valid_from > valid_to`(예: `12-20` ~ `02-28`)면 연말을 걸치는 구간으로 보고 `MM-DD >= valid_from or MM-DD <= valid_to`로 판정합니다. 기간 표기가 아예 없으면(학기 중 상시 시간표) 항상 유효한 것으로 취급합니다. 통합표(한 표 안에서 계절학기/단축근무/정상근무 라벨로 서로 다른 시각을 함께 표기)는 라벨별 기간이 각 trip에 반영되고, 같은 표기가 두 구간 이상이면(예: 정상근무가 학기 앞뒤로 두 번) trip이 구간 수만큼 나뉩니다.

**조회 응답**: `GET /bus/timetable?date=`는 저장 문서 중 조회일에 운행하는 문서 1건(사용자 수정본 우선, 다음은 최신)을 골라 그날 하루치를 방향별 `segments`로 바꿔 내보냅니다. 조회일이 어느 기간에도 들지 않으면 가장 최근 문서로 빈 시간표를 냅니다. 같은 문서 안 중복(방향·종류·시각)은 한 번만 넣습니다.
- `day_type`(`weekday`/`saturday`/`sunday`/`holiday`)은 `app/config/holidays.json`(2026·2027년, 대체공휴일 포함, 앱 목록에서 옮김)으로 정합니다. 원문 표 `주말`은 토·일 양쪽, `공휴일`은 holiday에 씁니다. 목록에 없는 해는 요일로만 판정하고 경고 로그를 남기므로, 매년 월력요항이 나오면 다음 해를 추가합니다
- 정류장(`stops`)은 `app/config/shuttle_stops.json`에서 관리합니다. `id`는 코드에서 고정이고 이름·좌표·탑승 안내만 고칩니다
- `version`은 날짜와 무관한 시간표 내용(trips·stops·notes·origin·needs_review)과 공휴일 파일 내용의 sha256 앞 16자입니다

**파싱 직후 검증**: 새 파싱본은 저장 전에 검사합니다(노선마다 반대 방향 한 쌍 이상, 수시운행·도착버스 구간의 시작 < 종료, 본문에 '토요일'이 있으면 토요일 편 1건 이상, 같은 place 직전 정상본 대비 편수 절반 미만 급감 금지). 실패하면 `WARNING` 로그를 남기고 직전 정상본을 그대로 내보내며 새 파싱본은 문서의 `pending`(검토 대기)에 둡니다. 응답 `source.needs_review`가 `true`가 됩니다. 비교할 정상본이 없거나 `force=true`로 동기화하면 새 파싱본을 쓰되 `problems`로 표시해 역시 `needs_review: true`입니다. 관리자가 PUT/관리자 화면으로 저장하면 풀립니다.

**사용자 직접 수정이 우선**: `PUT /bus/timetable/{place}`로 trips를 직접 입력하면 `source: "user"`로 저장되고, 이후 cron/refresh는 이 place를 다시 파싱하지 않고 건너뜁니다(로그만 남김). 되돌리려면 `DELETE /bus/timetable/{place}`로 사용자 수정본을 지우거나, `POST /bus/timetable/refresh?force=true`로 사용자 수정본과 해시 가드를 모두 무시하고 강제로 재파싱합니다.

자체 점검: `python tests/test_shuttle_timetable.py`

## 주요 API

셔틀 시간표 쓰기(PUT/DELETE/refresh)와 TUK World 쓰기는 `Authorization: Bearer <Keycloak access token>`이 필요합니다. 토큰은 JWKS로 서명을 검증하고 realm 롤 `KC_ADMIN_ROLE`(기본 `global_admin`)을 확인합니다. `KC_SERVER_URL`이 비어 있으면 쓰기는 모두 거부됩니다(503). 토큰이 없거나 무효면 401, 롤이 없으면 403입니다.

`main.py`에서 `root_path=/static-info`를 사용하므로, compose 기준 모든 엔드포인트는 `/static-info` 하위로 접근합니다.

- `GET /static-info/health`
- `GET /static-info/bus/images`
- `GET /static-info/bus/image/{index}`
- `GET /static-info/meal/images` (주간 식단표 iBook 페이지 이미지)
- `GET /static-info/meal/image/{index}`
- `GET /static-info/bus/timetable?date=YYYY-MM-DD` — 조회일(생략 시 오늘, KST) 하루치 셔틀 시간표. 응답 `date`, `day_type`, `holiday_name`, `version`, `valid_from`/`valid_to`(연도 포함), `source{title,pdf_url,parsed_at,origin,needs_review}`, `stops[]`, `routes[].directions[]`(고정 6개: `main_out`·`main_in`·`campus2_out`·`campus2_in`·`campus2_from_station`·`campus2_to_station`, 운행 없으면 `segments: []`), `notes[]`. segment는 `{"type":"fixed","time","note"}` 또는 `{"type":"frequent"|"arrival_board","start","end","note"}`이며 시작 시각 오름차순입니다. 날짜 형식 오류 422, 저장된 시간표가 없으면 503 `{"detail":"timetable_unavailable"}`. 응답 헤더 `ETag: "<version>"`, `Cache-Control: public, max-age=300`이며 `If-None-Match`가 현재 version과 같으면 본문 없이 304
- `POST /static-info/bus/timetable/refresh` (관리자 Bearer) — iBook 원본 PDF를 백그라운드에서 동기화(변경 시에만 재파싱), 즉시 202 반환. 쿼리 `force=true`면 사용자 수정본과 해시 가드를 모두 무시하고 강제 재파싱. 기동 시 자동 동기화되므로 최초 적재용 수동 호출은 불필요, 결과는 잠시 후 `GET /bus/timetable`로 확인
- `PUT /static-info/bus/timetable/{place}` (관리자 Bearer) — place의 시간표를 사용자가 직접 입력해 저장 (body `{"title": "...", "trips": [...], "notes": [...]}`, `title`은 보낸 값으로 저장되며 생략하면 null, `notes`는 생략하면 기존 안내문 유지, trip의 `kind`는 생략 가능). 이후 cron/refresh가 덮어쓰지 않음
- `DELETE /static-info/bus/timetable/{place}` (관리자 Bearer) — 사용자 수정본을 삭제해 다시 PDF 파싱 대상으로 되돌림
- `GET /static-info/organization/tree`
- `GET /static-info/organization/search/{name}`
- `GET /static-info/organization/{path}/children`
- `GET /static-info/organization/{path}`

## 운영 배포 (루트 compose)

데이터는 호스트 `${SANDOL_DATA_DIR}/static-info` 디렉터리 하나를 컨테이너 `/app/static-info`에 rw로 마운트해 보관합니다(`DATA_DIR=/app/static-info/data`, `SCHOOL_INFO_PATH=/app/static-info/school_info.json`). 컨테이너를 다시 만들어도 아래 파일은 유지됩니다.

| 파일 | 내용 |
|---|---|
| `school_info.json` | 조직도 원본. 관리자 화면에서 저장하면 같은 디렉터리에 `.bak`, `.meta.json`이 생깁니다 |
| `data/shuttle_timetable.json`, `data/shuttle_pdf/` | 셔틀 시간표(사용자 수정본 포함)와 원본 PDF |
| `data/tuk_world/` | TUK World SQLite와 이미지 |

조직도는 임시 파일을 쓴 뒤 `os.replace`로 교체하므로 파일 단위 바인드가 아니라 디렉터리 마운트가 필요합니다.

### 관리자 화면 (`/static-info/admin`)

1. Keycloak 운영 콘솔(realm `Sandori`)에서 static-info 전용 confidential client를 만들고, Standard flow와 PKCE(S256)를 켜고, redirect URI `https://<서비스 도메인>/static-info/admin-oauth/callback`을 등록합니다.
2. 서버 `sandol-static-info-service/.env`(루트 compose의 env_file)에 `ADMIN_PANEL_ENABLED=true`, `KC_CLIENT_ID`, `KC_CLIENT_SECRET`, `ADMIN_SESSION_KEY`(Fernet 키)를 넣습니다. `STATIC_INFO_PUBLIC_BASE_URL`, `KC_SERVER_URL`, `KC_JWKS_URL`, `KC_TOKEN_URL`, `DATA_DIR`, `SCHOOL_INFO_PATH`는 루트 compose가 `SERVICE_DOMAIN` 기준으로 채웁니다.
3. `docker compose up -d --no-deps static-info-service`로 재생성합니다.

값이 하나라도 비어 있으면 관리자 화면은 마운트되지 않고 `/static-info/admin`은 404입니다(fail-closed). 로그인은 `global_admin` 롤이 있는 계정만 성공하고, 롤은 로그인할 때만 확인하므로 롤을 회수해도 세션 수명(`ADMIN_SESSION_TTL_SECONDS`, 기본 3600초) 동안은 접근이 남습니다.

게이트웨이는 `X-Forwarded-Proto: https`를 보내고, 컨테이너의 uvicorn은 `--proxy-headers --forwarded-allow-ips=*`로 그 값을 믿어 관리자 화면 링크를 https로 만듭니다. TUK World 이미지 업로드(앱 한도 5MB)를 위해 게이트웨이 `client_max_body_size`는 6m입니다.
