FROM python:3.11-slim

# Install system dependencies
# xvfb: virtual display for the non-headless Playwright browser used to clear
# the AnimeDekho ad-gate (headless Chromium is served a CF challenge instead).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    python3-pip \
    libicu-dev \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install N_m3u8DL-RE binary (primary download engine)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
      URL="https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.5.1-beta/N_m3u8DL-RE_v0.5.1-beta_linux-arm64_20251029.tar.gz"; \
    else \
      URL="https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.5.1-beta/N_m3u8DL-RE_v0.5.1-beta_linux-x64_20251029.tar.gz"; \
    fi && \
    curl -L -o /tmp/N_m3u8DL-RE.tar.gz "$URL" && \
    tar -xzf /tmp/N_m3u8DL-RE.tar.gz -C /usr/local/bin/ && \
    rm /tmp/N_m3u8DL-RE.tar.gz && \
    chmod +x /usr/local/bin/N_m3u8DL-RE


# Set working directory
WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium + its OS deps (for the DGT gate unlock browser).
RUN python -m playwright install --with-deps chromium

# Copy the rest of the app
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Prevent N_m3u8DL-RE .NET runtime and Spectre.Console crash in headless containers
ENV TERM=xterm
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
ENV DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION=1
ENV COMPlus_EnableDiagnostics=0
ENV DOTNET_EnableDiagnostics=0

# Gate-unlock tunables (browser unlock is skipped gracefully if these fail)
ENV DGT_GATE_BROWSER=auto
ENV DGT_GATE_WAIT=42
ENV DGT_GATE_TIMEOUT=150

# Run under a virtual display so the non-headless Chromium can start without a
# real X server. Falls back to plain python if xvfb-run is unavailable.
CMD ["sh", "-c", "xvfb-run -a --server-args='-screen 0 1366x768x24' python main.py || python main.py"]
