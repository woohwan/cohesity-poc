FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 (lxml, pdfplumber 의존성)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY gaia_ragas/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY gaia_ragas/ .

ENV PYTHONUNBUFFERED=1

CMD ["bash"]
