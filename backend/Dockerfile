FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .
USER 65532:65532
CMD ["uvicorn", "stocks.api:app", "--host", "0.0.0.0", "--port", "8000"]
