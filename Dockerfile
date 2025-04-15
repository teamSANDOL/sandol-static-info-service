# 📌 Multi-Stage Dockerfile for FastAPI & Node.js Services

######################################
# FastAPI Service
######################################
FROM python:3.11 AS fastapi

# 작업 디렉토리 설정
WORKDIR /app

# 종속성 파일 복사 및 설치
COPY fastapi/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY fastapi/ .

# 환경 변수 설정 (필요 시 사용)
ENV APP_ENV=production

# 컨테이너 실행 시 기본 명령어 설정
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


######################################
# Node.js Service
######################################
FROM node:18 AS nodejs

# 작업 디렉토리 설정
WORKDIR /app

# 종속성 파일 복사 및 설치
COPY nodejs/package.json nodejs/package-lock.json ./
RUN npm install --omit=dev

# 애플리케이션 코드 복사
COPY nodejs/ .

# 환경 변수 설정 (필요 시 사용)
ENV NODE_ENV=production

# 컨테이너 실행 시 기본 명령어 설정
CMD ["node", "index.js"]
