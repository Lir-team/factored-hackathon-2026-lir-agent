#!/usr/bin/env bash
# Local Firestore for the case store: runs the emulator in Docker on the host network.
#
#   scripts/firestore-emulator.sh up     # start (or restart) the emulator
#   scripts/firestore-emulator.sh down   # stop and remove the container
#
# Then run the agent (or the Firestore tests) with:
#   FIRESTORE_EMULATOR_HOST=localhost:8086 CASE_STORE=firestore
set -euo pipefail

CONTAINER=lir-firestore-emulator
IMAGE=gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators
PROJECT="${FIRESTORE_PROJECT:-lir-local}"
EMULATOR_PORT="${EMULATOR_PORT:-8086}"

down() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

up() {
  down
  docker run -d --name "$CONTAINER" --network host "$IMAGE" \
    gcloud emulators firestore start --host-port="localhost:${EMULATOR_PORT}" \
    --project="$PROJECT" >/dev/null

  for _ in $(seq 1 60); do
    curl -sf "http://localhost:${EMULATOR_PORT}" >/dev/null && break
    sleep 1
  done
  curl -sf "http://localhost:${EMULATOR_PORT}" >/dev/null || {
    echo "Firestore emulator did not start; see: docker logs $CONTAINER" >&2
    exit 1
  }
  echo "Firestore emulator on localhost:${EMULATOR_PORT} (project ${PROJECT})"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
