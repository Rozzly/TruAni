FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -r -s /bin/false truani && \
    mkdir -p /app/data && chown truani:truani /app/data

COPY . .

USER truani

EXPOSE 5656

CMD ["python", "app.py"]
