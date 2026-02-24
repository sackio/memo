FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
COPY src/ src/

RUN uv pip install --system --no-cache .

# Pre-download tiktoken encoding data so the container works without outbound DNS
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

CMD ["python", "-m", "memo.main"]
