# Stage 1: build frontend
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ .
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim
WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
COPY src/ src/

RUN uv pip install --system --no-cache .

# Pre-download tiktoken encoding data so the container works without outbound DNS
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY --from=ui-builder /ui/dist /app/ui/dist

CMD ["python", "-m", "memo.main"]
