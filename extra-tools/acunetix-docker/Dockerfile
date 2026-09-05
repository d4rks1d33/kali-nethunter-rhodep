# Acunetix Professional v24.1 in a Docker container.
# Runs under QEMU user-mode on arm64 (--platform linux/amd64).
#
# Ports:
#   3443  — Acunetix HTTPS web UI
#   35432 — embedded PostgreSQL (internal, not published)
#
# Build:
#   docker build --platform linux/amd64 -t acunetix-local:latest .
#
# Run:
#   docker run -d --name=acunetix --platform linux/amd64 \
#     -p 3443:3443 \
#     -v acunetix_data:/home/acunetix/.acunetix \
#     --restart=no \
#     acunetix-local:latest
#
# Crack files (wvsc, license_info.json, wa_data.dat) must be in the same
# directory as this Dockerfile at build time.

FROM --platform=linux/amd64 ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    acunetix_containerized=yes \
    acunetix_accept_license=yes \
    acunetix_user=admin \
    acunetix_password=admin1234 \
    acunetix_email=admin@acunetix.local \
    acunetix_first_name=Admin \
    acunetix_last_name=User \
    acunetix_company=Pwn3rzs

# Runtime deps: the installer's shared_obj_deps + Chromium headless scanner.
# Ubuntu 22.04 has all these with the exact names Acunetix expects.
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl procps sudo \
        libx11-6 libxext6 libxfixes3 libxdamage1 libxrandr2 libxcomposite1 \
        libxcb1 libxkbcommon0 libdrm2 \
        libpango-1.0-0 libcairo2 libatk1.0-0 \
        libglib2.0-0 \
        libdbus-1-3 libexpat1 \
        libnss3 libnspr4 \
        libasound2 libcups2 && \
    apt-get install -y --no-install-recommends \
        libgbm1 libatk-bridge2.0-0 libatspi2.0-0 libgobject-2.0-0 libgcc-s1 \
        2>/dev/null || true && \
    rm -rf /var/lib/apt/lists/*

# Copy the installer and crack files.
COPY acunetix_24.1.240111130_x64.sh /tmp/acunetix.sh
COPY wvsc                            /tmp/wvsc
COPY license_info.json               /tmp/license_info.json
COPY wa_data.dat                     /tmp/wa_data.dat

# Install Acunetix non-interactively.
# --force skips the shared-lib dependency check (we installed them above).
# acunetix_containerized=yes tells the installer to skip systemd setup.
RUN chmod +x /tmp/acunetix.sh && \
    bash /tmp/acunetix.sh --force && \
    rm -f /tmp/acunetix.sh

# Apply the crack: replace wvsc and install the license files.
RUN VERSION=v_240111130 && \
    SCANNER_DIR=/home/acunetix/.acunetix/${VERSION}/scanner && \
    LICENSE_DIR=/home/acunetix/.acunetix/data/license && \
    mkdir -p "$SCANNER_DIR" "$LICENSE_DIR" && \
    # wvsc: the cracked scanner binary
    cp /tmp/wvsc "$SCANNER_DIR/wvsc" && \
    chown acunetix:acunetix "$SCANNER_DIR/wvsc" && \
    chmod +x "$SCANNER_DIR/wvsc" && \
    # license files: remove originals, copy cracked ones, lock immutable
    rm -f "$LICENSE_DIR/license_info.json" "$LICENSE_DIR/wa_data.dat" && \
    cp /tmp/license_info.json "$LICENSE_DIR/license_info.json" && \
    cp /tmp/wa_data.dat       "$LICENSE_DIR/wa_data.dat" && \
    chown acunetix:acunetix "$LICENSE_DIR/license_info.json" \
                             "$LICENSE_DIR/wa_data.dat" && \
    chmod 444 "$LICENSE_DIR/license_info.json" \
              "$LICENSE_DIR/wa_data.dat" && \
    chattr +i "$LICENSE_DIR/license_info.json" \
              "$LICENSE_DIR/wa_data.dat" && \
    rm -f /tmp/wvsc /tmp/license_info.json /tmp/wa_data.dat

# /etc/hosts entries required by the crack to redirect license validation.
RUN printf '\n127.0.0.1  erp.acunetix.com\n127.0.0.1  erp.acunetix.com.\n::1  erp.acunetix.com\n::1  erp.acunetix.com.\n' \
    >> /etc/hosts

# Acunetix web UI + scanner.
EXPOSE 3443

# Persist scan data, configs and the embedded PostgreSQL database.
VOLUME ["/home/acunetix/.acunetix"]

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
