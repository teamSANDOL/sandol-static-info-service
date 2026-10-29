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
alembic upgrade head
uvicorn main:app --host 0.0.0.0 --port 5600 --reload
```

- 로컬 기본 주소: `http://localhost:5600`
- health: `http://localhost:5600/health`
- OpenAPI 문서: `http://localhost:5600/docs`

## DB 마이그레이션 (Alembic)

TUK World·감사 로그 SQLite 스키마는 Alembic이 관리한다.

- 컨테이너는 기동할 때 `alembic upgrade head`를 먼저 실행한다(멱등).
- 예전에 `create_all`로 만들어져 `alembic_version`이 없는 기존 DB도 그대로 올라간다.
  이미 있는 테이블·컬럼·인덱스는 건너뛰고 버전만 기록한다.
- 로컬에서는 서버 실행 전에 `alembic upgrade head`를 한 번 실행한다.
- 모델에 컬럼·테이블을 추가하면 `alembic/versions/`에 새 리비전을 추가한다
  (`alembic revision -m "설명"`). 리비전은 inspector로 존재 여부를 확인하는 멱등 형태로 쓴다.

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

**운행 기간(`valid_from`/`valid_to`)**: 문서엔 기간이 `MM-DD`로만 적혀 있어 trip엔 `MM-DD`를 저장하고, 문서마다 **연도 기준일**(`ref_date`)을 함께 저장합니다. PDF는 파싱한 날, 관리자 저장본은 처음 만든 날(PDF 문서를 고치면 원래 기준일 유지)입니다. 기준일이 없는 옛 저장본은 파싱일 → 제목·place의 `NN년`(그해 7월 1일) → 수정일 순으로 추정합니다. 각 `MM-DD` 기간은 기준일 60일 전 이후에 끝나는 가장 이른 해로 연도를 붙이고, `valid_from > valid_to`(예: `12-20` ~ `02-28`)면 다음 해에 끝나는 것으로 봅니다. 문서 유효기간은 그 기간들의 처음~끝이며, 기간 표기가 전혀 없는 문서는 상시입니다. 통합표(계절학기/단축근무/정상근무 라벨)는 라벨별 기간이 각 trip에 반영됩니다.

**조회 응답**: `GET /bus/timetable?date=`는 조회일을 연도까지 포함하는 문서끼리만 비교해 1건을 고릅니다(관리자 문서 `source: "user"`가 기간 유무와 무관하게 먼저 — 기간 없는 관리자 문서는 상시라 매일 유효 —, 다음 기간 표기 있는 문서, 다음 최신). 지난해 수정본은 올해 날짜를 포함하지 않으므로 올해 PDF를 가리지 않습니다. 기간이 적힌 PDF 문서 안에 기간 표기 없는 페이지(상시편)가 섞여 있으면, 그 편도 문서 기간 안에서만 나옵니다(의도된 동작: 문서가 끝나면 그 문서의 모든 편이 끝남). 저장값 중 해석할 수 없는 편(예전 형식 `H:MM`은 `HH:MM`으로 고쳐 읽음)은 경고 로그를 남기고 건너뛰며 응답은 계속 나갑니다. 포함하는 문서가 없으면 가장 최근에 끝난 문서(없으면 가장 먼저 시작할 문서)로 빈 시간표를 내며, `valid_from`/`valid_to`는 그 문서의 실제 기간입니다(예: 2027-01-02 조회 → 2026-09-01~2026-12-22). 같은 문서 안 중복(방향·종류·시각)은 한 번만 넣습니다.
- `day_type`(`weekday`/`saturday`/`sunday`/`holiday`)은 `app/config/holidays.json`(2026·2027년, 대체공휴일 포함, 앱 목록에서 옮김)으로 정합니다. 원문 표 `주말`은 토·일 양쪽, `공휴일`은 holiday에 씁니다. 목록에 없는 해는 요일로만 판정하고 경고 로그를 남기므로, 매년 월력요항이 나오면 다음 해를 추가합니다
- 정류장(`stops`)은 `app/config/shuttle_stops.json`에서 관리합니다. `id`는 코드에서 고정이고 이름·좌표·탑승 안내만 고칩니다
- `version`은 날짜와 무관한 응답 재료(검토 대기본을 뺀 저장 문서 전체: trips·notes·출처·기간·연도 기준일, 정류장, needs_review, 공휴일 파일)의 sha256 앞 16자입니다

**파싱 직후 검증**: 새 파싱본은 저장 전에 검사합니다(노선마다 반대 방향 한 쌍 이상, 수시운행·도착버스 구간의 시작 < 종료, 표 머리(표 제목 괄호·운행일만 있는 줄)에 '토요일'이 있으면 토요일 편 1건 이상, 직전 정상본에 있던 노선 소실 금지, 같은 place 직전 정상본 대비 편수 절반 미만 급감 금지). 비교 대상은 같은 place의 직전 정상본, 학기가 바뀌어 새 place면 가장 최근 정상본이며 이때는 방학처럼 편수가 원래 줄 수 있어 급감 검사는 하지 않습니다. 실패하면 `WARNING` 로그를 남기고 같은 place의 직전 정상본을 그대로 내보내며 새 파싱본은 문서의 `pending`(검토 대기)에 둡니다. 응답 `source.needs_review`가 `true`가 됩니다. 같은 place의 정상본이 없거나 `force=true`로 동기화하면 새 파싱본을 쓰되 `problems`로 표시해 역시 `needs_review: true`입니다. 관리자가 PUT/관리자 화면으로 저장하면 풀립니다.

**사용자 직접 수정이 우선**: `PUT /bus/timetable/{place}`로 trips를 직접 입력하면 `source: "user"`로 저장되고, 이후 cron/refresh는 이 place를 다시 파싱하지 않고 건너뜁니다(로그만 남김). 되돌리려면 `DELETE /bus/timetable/{place}`로 사용자 수정본을 지우거나, `POST /bus/timetable/refresh?force=true`로 사용자 수정본과 해시 가드를 모두 무시하고 강제로 재파싱합니다.

자체 점검: `python tests/test_shuttle_timetable.py`

## 주요 API

셔틀 시간표 쓰기(관리자 API, PUT/DELETE/refresh)와 TUK World 쓰기는 `Authorization: Bearer <Keycloak access token>`이 필요합니다. 토큰은 JWKS로 서명을 검증하고, 셔틀 쓰기는 realm 롤 `SHUTTLE_ADMIN_ROLES`(쉼표 구분, 기본 `global_admin,shuttle_admin`) 중 하나, 그 밖의 쓰기는 `KC_ADMIN_ROLE`(기본 `global_admin`)을 확인합니다. `KC_SERVER_URL`이 비어 있으면 쓰기는 모두 거부됩니다(503). 토큰이 없거나 무효면 401, 롤이 없으면 403입니다.

`main.py`에서 `root_path=/static-info`를 사용하므로, compose 기준 모든 엔드포인트는 `/static-info` 하위로 접근합니다.

- `GET /static-info/health`
- `GET /static-info/bus/images`
- `GET /static-info/bus/image/{index}`
- `GET /static-info/meal/images` (주간 식단표 iBook 페이지 이미지)
- `GET /static-info/meal/image/{index}`
- `GET /static-info/bus/timetable?date=YYYY-MM-DD` — 조회일(생략 시 오늘, KST) 하루치 셔틀 시간표. 응답 `date`, `day_type`, `holiday_name`, `version`, `valid_from`/`valid_to`(연도 포함), `source{title,pdf_url,parsed_at,origin,needs_review}`, `stops[]`, `routes[].directions[]`(고정 6개: `main_out`·`main_in`·`campus2_out`·`campus2_in`·`campus2_from_station`·`campus2_to_station`, 운행 없으면 `segments: []`), `notes[]`. segment는 `{"type":"fixed","time","note"}` 또는 `{"type":"frequent"|"arrival_board","start","end","note"}`이며 시작 시각 오름차순입니다. 날짜 형식 오류 422, 저장된 시간표가 없으면 503 `{"detail":"timetable_unavailable"}`. 응답 헤더 `ETag: "<version>"`, `Cache-Control: public, max-age=300`이며 `If-None-Match`가 현재 태그와 같으면 본문 없이 304. `date`를 생략하면 태그는 `"<version>-<YYYYMMDD(KST)>"`라 다음 날 304로 어제 본문이 남지 않습니다. `version`은 날짜와 무관한 응답 재료(검토 대기본을 뺀 저장 문서 전체·정류장·needs_review·공휴일 파일)의 해시입니다
- `POST /static-info/bus/timetable/refresh` (관리자 Bearer) — iBook 원본 PDF를 백그라운드에서 동기화(변경 시에만 재파싱), 즉시 202 반환. 쿼리 `force=true`면 사용자 수정본과 해시 가드를 모두 무시하고 강제 재파싱(`KC_ADMIN_ROLE` 전체 관리자만, 셔틀 관리자는 403). 기동 시 자동 동기화되므로 최초 적재용 수동 호출은 불필요, 결과는 잠시 후 `GET /bus/timetable`로 확인
- `PUT /static-info/bus/timetable/{place}` (관리자 Bearer) — place의 시간표를 사용자가 직접 입력해 저장 (body `{"title": "...", "trips": [...], "notes": [...]}`, `title`은 보낸 값으로 저장되며 생략하면 null, `notes`는 생략하면 기존 안내문 유지, trip의 `kind`는 생략 가능). 이후 cron/refresh가 덮어쓰지 않음
- `DELETE /static-info/bus/timetable/{place}` (관리자 Bearer) — 사용자 수정본을 삭제해 다시 PDF 파싱 대상으로 되돌림
- 셔틀 관리자 API `/static-info/bus/admin/timetables` (셔틀 관리자 Bearer, 요청·응답 스키마는 OpenAPI `Shuttle Admin` 태그). 모든 쓰기는 `admin_audit` 감사 로그를 남기고 저장 즉시 공개 `GET /bus/timetable`과 `version`에 반영됩니다. 문서·운행편을 고치면 사용자 수정본(`source: "user"`)이 되어 force 없는 동기화가 더는 이 문서를 갱신하지 않고(목록·상세의 `auto_update: false`로 표시), 첫 파싱 실패 표시(`problems`)도 지워지며, PDF 문서의 연도 기준일은 그대로 유지됩니다. 목록·상세의 `revision`을 문서 `PATCH`·`DELETE`, 운행편 `POST`·`PUT`·`DELETE`에 `If-Match`로 보내면 그사이 다른 사람이 고친 경우 412(헤더 생략 가능, `If-Match: *`는 존재만 확인). 관리자 상세는 운행편을 원시값 그대로 보여 주고 입력 규칙에 어긋나는 옛 편엔 `problems`를 적으므로 그 편도 `PUT`·`DELETE`로 고칠 수 있습니다(공개 GET에서만 건너뜀). **주의**: 유효기간 없이 만든 관리자 문서는 상시라 매일 유효하고 관리자 문서가 우선하므로, 해가 바뀌어도 새 PDF 문서를 계속 가립니다(목록·상세의 `always_active: true`). 학기 문서는 유효기간을 넣으세요. place는 `/`·`\`·제어문자 없이 100자 이내(422). 감사 로그의 대상 값은 따옴표·줄바꿈을 이스케이프합니다
  - `GET` — 문서 목록(place·제목·출처·연도 포함 유효기간·편수·검토 대기 여부와 요약)
  - `POST` — 문서 생성 `{"place","title","valid_from":"YYYY-MM-DD","valid_to":"YYYY-MM-DD","notes":[],"trips":[...]}` (유효기간을 둘 다 비우면 상시, 같은 place면 409)
  - `GET /{place}` — 문서 상세(운행편마다 안정 `id`)
  - `PATCH /{place}` — 제목·유효기간(둘을 함께)·안내문 수정
  - `DELETE /{place}` — 문서 삭제
  - `POST /{place}/trips`, `PUT /{place}/trips/{id}`, `DELETE /{place}/trips/{id}` — 운행편 추가·수정·삭제. 운행편은 `{"direction": "main_out|main_in|campus2_out|campus2_in|campus2_from_station|campus2_to_station", "day_type": "평일|토요일|일요일|주말|공휴일", "kind": "fixed|frequent|arrival_board", "time": "HH:MM", "end": "HH:MM|null", "note", "day_note", "boarding_place", "valid_from"/"valid_to"(MM-DD, 보통 비움), "period_note"}`이며, fixed는 `end` 없음, 구간은 `time < end`. 형식이 틀리면 422
  - `POST /{place}/review/approve` — 검토 대기 파싱본을 정상본으로 승격(첫 파싱 실패 표시면 표시만 해제). 검토 대기본이 생긴 뒤 문서를 고쳤다면 수정이 사라지므로 409이고 `?force=true`일 때만 덮어씀(force는 `KC_ADMIN_ROLE` 전체 관리자만), `POST /{place}/review/reject` — 검토 대기본을 버리고 같은 PDF는 다시 파싱하지 않음. 검토 대기가 없으면 409
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
