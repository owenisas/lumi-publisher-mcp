FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY lumi_publisher_mcp ./lumi_publisher_mcp
RUN pip install --no-cache-dir .
ENTRYPOINT ["lumi-publisher-mcp"]
