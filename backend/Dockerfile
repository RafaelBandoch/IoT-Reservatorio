FROM python:3.12-slim

# Logs sem buffer, para aparecerem no "docker compose logs -f"
ENV PYTHONUNBUFFERED=1

# Raízes extras (antivírus ou proxy que intercepta HTTPS), se houver.
# Ver ca-extra/LEIAME.md. Sem nenhum .crt, nada muda.
COPY ca-extra/ /tmp/ca-extra/
RUN if ls /tmp/ca-extra/*.crt >/dev/null 2>&1; then \
      cp /tmp/ca-extra/*.crt /usr/local/share/ca-certificates/ && update-ca-certificates; \
    fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Aplica as migrações pendentes e sobe a API.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
