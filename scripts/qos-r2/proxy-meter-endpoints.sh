#!/bin/sh
# Strict registry generator; counter changes require a settled migration.
set -eu
HELPER=${PM_IO_HELPER:-$(dirname "$0")/proxy-meter-io.py}
PYTHON=${PM_PYTHON:-python3}
LOCK=${PM_LOCK:-/etc/routermgmt/state/proxy-meter-v3.lock}
CONFIG=${EP_CONFIG:-/etc/mihomo/config.yaml}
PROVIDERS=${EP_PROVIDERS_DIR:-/etc/mihomo/proxy_providers}
RULES=${EP_RULES_OUT:-/etc/routermgmt/proxy-meter-rules-v3.conf}
COVER=${EP_COVER_OUT:-/etc/routermgmt/proxy-meter-coverage-v3.json}
if [ "${1:-}" = "--check" ]; then
 exec "$PYTHON" "$HELPER" generate --config "$CONFIG" --providers "$PROVIDERS" --rules "$RULES" --cover "$COVER" --check
fi
exec 9>"$LOCK"
flock -n 9 || { echo 'FAIL registry: meter lock held'; exit 3; }
exec "$PYTHON" "$HELPER" generate --config "$CONFIG" --providers "$PROVIDERS" --rules "$RULES" --cover "$COVER"
