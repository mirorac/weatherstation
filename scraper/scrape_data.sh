#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status,
# treat unset variables as an error, and ensure pipeline failures propagate.
set -euo pipefail

# Automatically load .env file if present in the current or script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${SCRIPT_DIR}/.env"
    set +a
elif [[ -f ".env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source ".env"
    set +a
fi

# Tuya Cloud API Credentials & Target Settings (read from environment / .env)
CLIENT_ID="${CLIENT_ID:?CLIENT_ID environment variable is required}"
CLIENT_SECRET="${CLIENT_SECRET:?CLIENT_SECRET environment variable is required}"
BASE_URL="${BASE_URL:-https://openapi.tuyaeu.com}"
DEVICE_ID="${DEVICE_ID:-bf637c44012ca3c7979ia7}"
INTERVAL="${INTERVAL:-300}"
API_URL="${API_URL:-http://api:5000/api/data}"
INGEST_API_KEY="${INGEST_API_KEY:-}"

TOKEN_PATH="/v1.0/token?grant_type=1"

# SHA256 hash of an empty request body (used in signature calculations)
EMPTY_BODY_SHA256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Request an OAuth access token from Tuya Cloud API
get_token() {
    local t sign response

    # Current epoch timestamp in milliseconds
    t=$(python3 -c 'import time; print(int(time.time() * 1000))')

    # Construct the string to sign according to Tuya API spec
    string_to_sign="GET
${EMPTY_BODY_SHA256}

${TOKEN_PATH}"

    # Generate HMAC-SHA256 signature using CLIENT_SECRET
    sign=$(
        printf '%s' "${CLIENT_ID}${t}${string_to_sign}" |
        openssl dgst -sha256 -hmac "$CLIENT_SECRET" -binary |
        xxd -p -c 256 |
        tr '[:lower:]' '[:upper:]'
    )

    # Fetch access token
    response=$(
        curl -fsS \
            "${BASE_URL}${TOKEN_PATH}" \
            -H "client_id: ${CLIENT_ID}" \
            -H "sign: ${sign}" \
            -H "sign_method: HMAC-SHA256" \
            -H "t: ${t}"
    )

    ACCESS_TOKEN=$(echo "$response" | jq -r '.result.access_token')

    # Validate received token
    if [[ -z "$ACCESS_TOKEN" || "$ACCESS_TOKEN" == "null" ]]; then
        echo "Failed to obtain Tuya access token:" >&2
        echo "$response" >&2
        return 1
    fi

    echo "Obtained Tuya access token"
}

# Fetch device shadow properties
get_properties() {
    local t path string_to_sign sign response

    path="/v2.0/cloud/thing/${DEVICE_ID}/shadow/properties"
    t=$(python3 -c 'import time; print(int(time.time() * 1000))')

    # Build signature string for v2.0 endpoint
    string_to_sign=$(printf 'GET\n%s\n\n%s' "$EMPTY_BODY_SHA256" "$path")

    # Generate signature using CLIENT_ID + ACCESS_TOKEN + timestamp + string_to_sign
    sign=$(
        printf '%s' "${CLIENT_ID}${ACCESS_TOKEN}${t}${string_to_sign}" |
        openssl dgst -sha256 -hmac "$CLIENT_SECRET" -binary |
        xxd -p -c 256 |
        tr '[:lower:]' '[:upper:]'
    )

    response=$(
        curl -fsS \
            "${BASE_URL}${path}" \
            -H "accept: */*" \
            -H "access_token: ${ACCESS_TOKEN}" \
            -H "client_id: ${CLIENT_ID}" \
            -H "content-type: application/json" \
            -H "sign: ${sign}" \
            -H "sign_method: HMAC-SHA256" \
            -H "signversion: 2.0" \
            -H "t: ${t}"
    )

    echo "$response"
}

# Obtain an initial access token
get_token

# Main polling loop
while true; do
    DATE=$(date +%Y-%m-%d)
    OUTPUT="data/${DATE}.jsonl"

    # Fetch and append property data
    if response=$(get_properties); then
        if echo "$response" | jq -e '.success == false and .code == 1010' >/dev/null; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') Tuya token invalid; refreshing token"
            get_token
            response=$(get_properties) || response=""
        fi

        # Persist and forward only successful property responses.
        if echo "$response" | jq -ce '.success == true and (.result.properties | type == "array")' >/dev/null; then
            echo "$response" | jq -c . >> "$OUTPUT"
            echo "$(date '+%Y-%m-%d %H:%M:%S') OK (saved to $OUTPUT)"

            # Forward new data to API server if available (non-blocking failure)
            if [[ -n "${API_URL:-}" ]]; then
                auth_args=()
                if [[ -n "${INGEST_API_KEY:-}" ]]; then
                    auth_args=(-H "X-API-Key: ${INGEST_API_KEY}")
                fi
                curl -sS -X POST "$API_URL" \
                    -H "Content-Type: application/json" \
                    "${auth_args[@]}" \
                    -d "$response" \
                    --max-time 5 >/dev/null 2>&1 || echo "$(date '+%Y-%m-%d %H:%M:%S') Warning: Failed to push to API at $API_URL" >&2
            fi
        else
            echo "$(date '+%Y-%m-%d %H:%M:%S') Invalid Tuya property response" >&2
            echo "$response" >&2
        fi
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') Request failed" >&2
    fi

    # Calculate sleep duration to align with fixed interval boundaries on the clock,
    # compensating for request latency and avoiding drift over time
    sleep_sec=$(python3 -c "import time; interval=$INTERVAL; print(max(0.1, interval - (time.time() % interval)))")
    sleep "$sleep_sec"
done