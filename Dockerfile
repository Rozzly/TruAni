FROM python:3.13-slim

WORKDIR /app

# Marks this as a container deployment so the in-app updater redirects users to
# an image rebuild instead of attempting a self-update (which a container wipes
# on recreate). See services/updater.py:_is_containerized().
ENV TRUANI_DEPLOYMENT=docker

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/* && \
    useradd -r -s /bin/false truani && \
    mkdir -p /app/data && chown truani:truani /app/data

COPY . .

EXPOSE 5656

ENTRYPOINT ["/app/entrypoint.sh"]
