#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

export UPDATE_FREQUENCY="$(bashio::config 'update_frequency')"
export MIN_TEMP="$(bashio::config 'min_temp')"
export MAX_TEMP="$(bashio::config 'max_temp')"
export DEFAULT_TARGET="$(bashio::config 'default_target')"
export DEFAULT_DURATION_HOURS="$(bashio::config 'default_duration_hours')"
export MAX_DURATION_HOURS="$(bashio::config 'max_duration_hours')"
export OFFLINE_GRACE_SECONDS="$(bashio::config 'offline_grace_seconds')"

if bashio::config.true 'debug'; then
  export DEBUG=1
fi

# Pull MQTT broker details from the Supervisor-provided service.
if bashio::services.available "mqtt"; then
  export MQTT_HOST="$(bashio::services mqtt 'host')"
  export MQTT_PORT="$(bashio::services mqtt 'port')"
  export MQTT_USERNAME="$(bashio::services mqtt 'username')"
  export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
  bashio::log.info "Using MQTT broker ${MQTT_HOST}:${MQTT_PORT}"
else
  bashio::log.warning "No MQTT service available; set one up (e.g. Mosquitto)."
fi

bashio::log.info "Starting Huum MQTT bridge..."
cd /app
exec python3 -m huum.main
