FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 IRON_MODE=hosted IRON_DATA_DIR=/data
WORKDIR /srv/iron-coach
COPY requirements-web.lock ./
RUN pip install --no-cache-dir -r requirements-web.lock && groupadd --gid 10001 iron && useradd --uid 10001 --gid iron --no-create-home iron && mkdir /data && chown iron:iron /data
COPY app ./app
COPY web ./web
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import os,urllib.request,urllib.parse; req=urllib.request.Request('http://127.0.0.1:8000/ready',headers={'Host':urllib.parse.urlsplit(os.environ['IRON_PUBLIC_URL']).netloc}); urllib.request.urlopen(req,timeout=3).close()"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1","--proxy-headers","--forwarded-allow-ips","*"]
