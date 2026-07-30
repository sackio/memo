# Stage 1: build frontend
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ .
RUN npm run build

# Stage 2: test runner.
#
# Deliberately NOT the last stage — docker's default target is the final stage,
# and `docker compose build` must keep producing the runtime image.
# Run the suite with:
#   docker build --target test -t memo-v2-test . && docker run --rm memo-v2-test
#
# Tests run HERE rather than on the host on purpose: the project requires
# Python >=3.12 (host is 3.10) and the host's fastapi/starlette versions are
# mismatched badly enough that `import memo.main` raises inside FastAPI's own
# constructor. This stage has the real, pinned dependency set.
FROM python:3.12-slim AS test
WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
COPY src/ src/
COPY migrations/ migrations/

RUN uv pip install --system --no-cache ".[dev]"
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY tests/ tests/

# OPENROUTER_API_KEY is a required setting; tests never make embedding calls
# (they pass dummy vectors), so a dummy value is enough to construct Settings.
ENV OPENROUTER_API_KEY=test-dummy-key
ENV DEFAULT_DB_PATH=/tmp/memo-test.db

# ENTRYPOINT + CMD, not CMD alone: `docker compose run --rm test <args>`
# REPLACES the CMD rather than appending to it, so with a bare CMD any attempt
# to pass pytest flags makes docker try to exec the flag itself
# ("exec: \"-q\": executable file not found"). With pytest as the entrypoint,
# args append the way you'd expect:
#   docker compose run --rm test                          -> tests/ -q
#   docker compose run --rm test tests/unit -q            -> that subset
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/", "-q"]

# Stage 3: Python runtime
FROM python:3.12-slim
WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
COPY src/ src/
COPY migrations/ migrations/

RUN uv pip install --system --no-cache .

# Pre-download tiktoken encoding data so the container works without outbound DNS
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY --from=ui-builder /ui/dist /app/ui/dist

CMD ["python", "-m", "memo.main"]
