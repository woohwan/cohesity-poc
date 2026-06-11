FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 (lxml, pdfplumber 의존성)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY qdrant_rag/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# gaia_ragas 파서 재사용 (sys.path 없이 직접 import)
COPY gaia_ragas/dart_xml_parser.py .

COPY qdrant_rag/ .

ENV PYTHONUNBUFFERED=1
ENV QDRANT_URL=http://qdrant:6333

CMD ["bash"]
