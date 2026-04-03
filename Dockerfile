FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/* && \
    useradd -r -s /bin/false truani && \
    mkdir -p /app/data && chown truani:truani /app/data

COPY . .

EXPOSE 5656

ENTRYPOINT ["/app/entrypoint.sh"]
