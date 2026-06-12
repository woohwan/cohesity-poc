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

# 호스트 사용자 UID/GID와 일치하는 사용자 생성 (getpwuid 오류 방지, 볼륨 권한 일치)
ARG UID=1000
ARG GID=1000
RUN groupadd -f -g ${GID} appgroup && \
    useradd -u ${UID} -g ${GID} -m -d /home/appuser appuser && \
    mkdir -p /app/output /app/hf_cache && \
    chown -R appuser:appgroup /app

USER appuser
ENV HOME=/home/appuser

ENV PYTHONUNBUFFERED=1
ENV QDRANT_URL=http://qdrant:6333

CMD ["bash"]
