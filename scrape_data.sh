#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status,
# treat unset variables as an error, and ensure pipeline failures propagate.
set -euo pipefail

# Tuya Cloud API Credentials
CLIENT_ID="rhncamrcg4sm3uehtkca"
CLIENT_SECRET="86b6253f578c45bfb8be432d994962ed"

# API Endpoint & Target Device
BASE_URL="https://openapi.tuyaeu.com"
DEVICE_ID="bf637c44012ca3c7979ia7"

# Polling interval in seconds (300s = 5 minutes)
INTERVAL=300

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

    # Build signature string for v2.0 endpoint (HTTPMethod + "\n" + Content-SHA256 + "\n" + Headers + "\n" + URL)
    string_to_sign=$(
        printf 'GET\n%s\n\n%s' \
            "$EMPTY_BODY_SHA256" \
            "$path"
    )

    # Generate signature using CLIENT_ID + ACCESS_TOKEN + timestamp + string_to_sign
    sign=$(
        printf '%s' "${CLIENT_ID}${ACCESS_TOKEN}${t}${string_to_sign}" |
        openssl dgst -sha256 -hmac "$CLIENT_SECRET" -binary |
        xxd -p -c 256 |
        tr '[:lower:]' '[:upper:]'
    )

    # Perform GET request for device properties
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
        # Ensure the response is valid JSON before appending
        if echo "$response" | jq -ce . >/dev/null; then
            echo "$response" | jq -c . >> "$OUTPUT"
            echo "$(date '+%Y-%m-%d %H:%M:%S') OK"
        else
            echo "$(date '+%Y-%m-%d %H:%M:%S') Invalid JSON response" >&2
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
