# Nessus Professional cracked -- Ubuntu 22.04 amd64 under QEMU on arm64.
#
# Replicates the xiv3r crack script (nessus_update_debian_only.sh) exactly:
#   1. Install Nessus debian10_amd64 .deb
#   2. Start nessusd briefly to initialise global.db (as the script does)
#   3. Stop it, apply all nessuscli fixes
#   4. Create admin user (expect)
#   5. Install plugin feed
#   6. Write plugin_feed_info.inc (ProfessionalFeed)
#   7. Copy to lib/nessus/plugins/ -- no chattr (not supported in build)
#
# Build (copy all-2.0.tar.gz here first):
#   docker build --platform linux/amd64 -t nessus-pro:latest .
#
# Run:
#   docker run -d --name=nessus --platform linux/amd64 \
#     --restart=no -p 8834:8834 \
#     --cap-add=NET_RAW --cap-add=NET_ADMIN \
#     -v nessus_data:/opt/nessus/var \
#     nessus-pro:latest

FROM --platform=linux/amd64 ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Prerequisites from the crack script + expect for adduser
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates dpkg expect procps && \
    rm -rf /var/lib/apt/lists/*

# Download and install Nessus (same URL as the crack script)
RUN curl -A Mozilla -fsSL \
    'https://www.tenable.com/downloads/api/v2/pages/nessus/files/Nessus-latest-debian10_amd64.deb' \
    -o /tmp/nessus.deb && \
    dpkg -i /tmp/nessus.deb && \
    rm -f /tmp/nessus.deb

# STEP: init global.db (same as "systemctl start" + "systemctl stop" in the crack script).
# The script starts nessusd once to let it initialise the DB, then stops it.
# Without this the global.db is empty and nessuscli fixes don't stick.
RUN /opt/nessus/sbin/nessus-service -q & \
    NPID=$! && \
    echo "waiting for global.db..." && \
    for i in $(seq 1 120); do \
        [ -f /opt/nessus/var/nessus/global.db ] && break; \
        sleep 2; \
    done && \
    echo "global.db ready, sleeping 5s..." && \
    sleep 5 && \
    echo "stopping nessusd..." && \
    kill -TERM $NPID 2>/dev/null && \
    wait $NPID 2>/dev/null || true

# STEP: apply nessuscli fixes (same as crack script)
RUN /opt/nessus/sbin/nessuscli fix --set ui_theme=dark && \
    /opt/nessus/sbin/nessuscli fix --set safe_checks=false && \
    /opt/nessus/sbin/nessuscli fix --set backend_log_level=performance && \
    /opt/nessus/sbin/nessuscli fix --set auto_update=false && \
    /opt/nessus/sbin/nessuscli fix --set auto_update_ui=false && \
    /opt/nessus/sbin/nessuscli fix --set disable_core_updates=true && \
    /opt/nessus/sbin/nessuscli fix --set report_crashes=false && \
    /opt/nessus/sbin/nessuscli fix --set send_telemetry=false

# STEP: create admin user with expect (same sequence as crack script)
COPY adduser.exp /tmp/adduser.exp
RUN expect /tmp/adduser.exp && rm -f /tmp/adduser.exp

# STEP: install plugin feed (baked into image -- no internet needed at runtime)
COPY all-2.0.tar.gz /tmp/all-2.0.tar.gz
RUN /opt/nessus/sbin/nessuscli update /tmp/all-2.0.tar.gz && \
    rm -f /tmp/all-2.0.tar.gz

# STEP: write plugin_feed_info.inc (ProfessionalFeed -- the crack)
RUN VERNUM=$(curl -sA Mozilla https://plugins.nessus.org/v2/plugins.php 2>/dev/null || echo "202609041258") && \
    printf 'PLUGIN_SET = "%s";\nPLUGIN_FEED = "ProfessionalFeed (Direct)";\nPLUGIN_FEED_TRANSPORT = "Tenable Network Security Lightning";\n' \
        "$VERNUM" > /opt/nessus/var/nessus/plugin_feed_info.inc && \
    cp /opt/nessus/var/nessus/plugin_feed_info.inc \
       /opt/nessus/lib/nessus/plugins/plugin_feed_info.inc 2>/dev/null || true

EXPOSE 8834

VOLUME ["/opt/nessus/var"]

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
