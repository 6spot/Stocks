FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --prefer-binary --no-cache-dir --timeout 120 --retries 5 .
USER 65532:65532
CMD ["uvicorn", "stocks.api:app", "--host", "0.0.0.0", "--port", "8000"]
