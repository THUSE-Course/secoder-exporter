FROM python:3.15-slim@sha256:490c3463f3766091067a01528e6be3208104eebcca11a306beab5de9672ee893
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.lock ./
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip check
COPY *.py ./
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "", "--port", "8000"]
