#!/usr/bin/env bash
# Local Pub/Sub for the case flow: runs the emulator in Docker, creates the `lir-cases`
# topic and a push subscription that delivers each case to the agent's /pubsub/push.
#
#   scripts/pubsub-emulator.sh up     # start (or restart) the emulator and create both
#   scripts/pubsub-emulator.sh down   # stop and remove the container
#
# Then run the agent with:
#   PUBSUB_EMULATOR_HOST=localhost:8085 CASES_PUBLISHER=pubsub PUBSUB_VERIFY_TOKEN=false
# (the emulator sends no OIDC token). The container shares the host network, so it pushes
# to localhost:$AGENT_PORT without crossing a host firewall (e.g. ufw blocks docker0).
set -euo pipefail

CONTAINER=lir-pubsub-emulator
IMAGE=gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators
PROJECT="${PUBSUB_PROJECT:-lir-local}"
TOPIC="${CASES_TOPIC:-lir-cases}"
SUBSCRIPTION="${TOPIC}-push"
EMULATOR_PORT="${EMULATOR_PORT:-8085}"
AGENT_PORT="${AGENT_PORT:-8080}"
API="http://localhost:${EMULATOR_PORT}/v1/projects/${PROJECT}"

down() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

up() {
  down
  docker run -d --name "$CONTAINER" --network host "$IMAGE" \
    gcloud beta emulators pubsub start --host-port="localhost:${EMULATOR_PORT}" \
    --project="$PROJECT" >/dev/null

  for _ in $(seq 1 60); do
    curl -sf "http://localhost:${EMULATOR_PORT}" >/dev/null && break
    sleep 1
  done
  curl -sf "http://localhost:${EMULATOR_PORT}" >/dev/null || {
    echo "Pub/Sub emulator did not start; see: docker logs $CONTAINER" >&2
    exit 1
  }

  curl -sf -X PUT "${API}/topics/${TOPIC}" >/dev/null
  curl -sf -X PUT "${API}/subscriptions/${SUBSCRIPTION}" \
    -H 'Content-Type: application/json' \
    -d "{\"topic\": \"projects/${PROJECT}/topics/${TOPIC}\",
         \"enableMessageOrdering\": true,
         \"ackDeadlineSeconds\": 120,
         \"pushConfig\": {\"pushEndpoint\": \"http://localhost:${AGENT_PORT}/pubsub/push\"}}" \
    >/dev/null

  echo "Pub/Sub emulator on localhost:${EMULATOR_PORT} (project ${PROJECT})"
  echo "  topic        projects/${PROJECT}/topics/${TOPIC}"
  echo "  subscription projects/${PROJECT}/subscriptions/${SUBSCRIPTION}"
  echo "  pushes to    http://localhost:${AGENT_PORT}/pubsub/push"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
