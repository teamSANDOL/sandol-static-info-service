# sandol-static-info-service

한국공학대학교 관련 정적 정보를 제공하는 FastAPI 기반 API 서비스입니다.

## 개요

- 프레임워크: `FastAPI`
- Python 버전: `3.11` (`>=3.11,<3.12`)
- 주요 의존성: `uvicorn`, `httpx`, `python-dotenv`
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
- `SECRET_KEY`: compose 환경에서 주입되는 키

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

## 주요 API

`main.py`에서 `root_path=/static-info`를 사용하므로, compose 기준 모든 엔드포인트는 `/static-info` 하위로 접근합니다.

- `GET /static-info/health`
- `GET /static-info/bus/images`
- `GET /static-info/bus/image/{index}`
- `GET /static-info/organization/tree`
- `GET /static-info/organization/search/{name}`
- `GET /static-info/organization/{path}/children`
- `GET /static-info/organization/{path}`
