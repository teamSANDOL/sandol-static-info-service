# Use the official Python image from the Docker Hub
FROM tiangolo/uvicorn-gunicorn-fastapi:python3.11

# Set the working directory
WORKDIR /app

# Copy the requirements.txt file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the entire 'sandol' directory to the working directory
COPY . /app

# Expose port 80
EXPOSE 80

# Command to run the FastAPI application
# 기동 전에 DB 스키마를 최신으로 올린다(alembic upgrade head는 멱등).
# 게이트웨이의 X-Forwarded-Proto를 믿어 관리자 화면 URL을 https로 만든다.
# 컨테이너는 내부망에서만 접근되므로 모든 프록시 IP를 허용한다.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 80 --proxy-headers --forwarded-allow-ips='*'"]
