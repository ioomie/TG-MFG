FROM public.ecr.aws/docker/library/python:3.13.16-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c
WORKDIR /app
COPY portal.py ./
COPY web ./web
COPY downloads ./downloads
USER 65534:65534
EXPOSE 7333
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-c", "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:7333/health', timeout=3)"]
CMD ["python", "-B", "portal.py", "--origin", "http://localhost:7333"]
