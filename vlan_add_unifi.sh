#!/usr/bin/env bash
set -euo pipefail

###############################################################################
#   WARNING: TLS CERTIFICATE VERIFICATION IS DISABLED (-k)
#   Unsafe on untrusted networks
#   USE AT YOUR OWN RISK
###############################################################################

BASE_URL="UCK-IP"
SITE_ID="SITE-UUID"
API_KEY="API-KEY"

DEBUG="${DEBUG:-0}"      # DEBUG=1 ./vlan_add_unifi.sh
DRY_RUN="${DRY_RUN:-0}"  # DRY_RUN=1 ./vlan_add_unifi.sh
CURL_TIMEOUT="${CURL_TIMEOUT:-20}"

NETWORKS_URL="${BASE_URL}/proxy/network/integration/v1/sites/${SITE_ID}/networks"

hdr=(-H "X-API-KEY: ${API_KEY}" -H "Accept: application/json" -H "Content-Type: application/json")

ts() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*"; }
debug() { [[ "$DEBUG" == "1" ]] && echo "[$(ts)] [DEBUG] $*" >&2; }
fail() { echo "[$(ts)] [ERROR] $*" >&2; }

trap 'fail "Unexpected error at line ${LINENO}: ${BASH_COMMAND}"; exit 1' ERR

curl_insecure() {
  curl -k -sS \
    --connect-timeout "${CURL_TIMEOUT}" \
    --max-time "${CURL_TIMEOUT}" \
    "$@"
}

CREATED=0
SKIPPED=0
FAILED=0

summary() {
  echo
  log "===== SUMMARY ====="
  log "Created : ${CREATED}"
  log "Skipped : ${SKIPPED}"
  log "Failed  : ${FAILED}"
}
trap summary EXIT

existing_vlans_json="[]"
have_vlan_cache=0

fetch_existing() {
  log "Fetching existing networks (for VLAN existence checks)..."

  local resp code body
  resp="$(curl_insecure -w "\n%{http_code}" "${hdr[@]}" "${NETWORKS_URL}")"
  code="$(echo "$resp" | tail -n1)"
  body="$(echo "$resp" | sed '$d')"

  debug "LIST HTTP code: ${code}"

  if [[ "${code}" =~ ^2 ]]; then
    existing_vlans_json="${body}"
    have_vlan_cache=1
    debug "Existing networks payload size: ${#existing_vlans_json} bytes"
    return 0
  fi

  fail "LIST returned HTTP ${code}. Continuing without cache (no skip-on-exists)."
  fail "LIST response: ${body:0:400}"
  have_vlan_cache=0
  return 0
}

vlan_exists() {
  local vid="$1"
  [[ "${have_vlan_cache}" == "1" ]] || return 1
  echo "${existing_vlans_json}" | grep -Eq "\"vlanId\"[[:space:]]*:[[:space:]]*${vid}\b"
}

refresh_existing() { fetch_existing; }

create_vlan() {
  local vid="$1"
  local name="$2"

  log "Processing VLAN ${vid} (${name})"

  if vlan_exists "${vid}"; then
    log "VLAN ${vid} already exists → skipping"
    SKIPPED=$((SKIPPED+1))
    return 0
  fi

  local payload
  payload="$(cat <<JSON
{
  "management": "UNMANAGED",
  "name": "${name}",
  "enabled": true,
  "vlanId": ${vid}
}
JSON
)"
  debug "POST ${NETWORKS_URL}"
  debug "Payload: ${payload}"

  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY-RUN] Would create VLAN ${vid} (${name})"
    CREATED=$((CREATED+1))
    return 0
  fi

  local resp code body
  resp="$(curl_insecure -w "\n%{http_code}" -X POST \
    "${hdr[@]}" \
    -d "${payload}" \
    "${NETWORKS_URL}")"

  code="$(echo "$resp" | tail -n1)"
  body="$(echo "$resp" | sed '$d')"

  if [[ "${code}" =~ ^2 ]]; then
    log "Created VLAN ${vid} (${name}) (HTTP ${code})"
    CREATED=$((CREATED+1))
    refresh_existing
    return 0
  fi

  fail "Failed to create VLAN ${vid} (${name}) (HTTP ${code})"
  fail "Response body: ${body:0:600}"
  FAILED=$((FAILED+1))
  return 0
}
fetch_existing
# =============================================================================
# VLAN CREATE LIST
# =============================================================================
# ---- Put your create_vlan lines below this line ----
# create_vlan 2 "NiceVlan2"
create_vlan 2137 "PapajNet"
# =============================================================================
# VLAN LIST ENDE
# =============================================================================

echo "Done."
