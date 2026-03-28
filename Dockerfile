FROM python:3.10-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser and system dependencies
RUN playwright install --with-deps chromium

# Default: run full pipeline; override CMD or use docker-compose for cartoon_to_slides
CMD ["python", "cartoon_to_slides.py", "--help"]
