#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID=${PROJECT_ID:-campusgpt-457123}
REGION=${REGION:-us-central1}
CLUSTER=${CLUSTER:-load-shed}
NODE_ACCOUNT="load-shed-nodes@${PROJECT_ID}.iam.gserviceaccount.com"
if ! gcloud artifacts repositories describe load-shed --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create load-shed --repository-format=docker --location="$REGION" --project="$PROJECT_ID"
fi
if ! gcloud iam service-accounts describe "$NODE_ACCOUNT" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create load-shed-nodes --project="$PROJECT_ID"
  sleep 10 # Allow IAM to propagate the new identity before granting roles.
fi
for role in roles/container.defaultNodeServiceAccount roles/artifactregistry.reader; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$NODE_ACCOUNT" --role="$role" --condition=None --quiet --format='value(etag)'
done
if ! gcloud container clusters describe "$CLUSTER" --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud container clusters create-auto "$CLUSTER" --region="$REGION" --project="$PROJECT_ID" --service-account="$NODE_ACCOUNT" --release-channel=regular --async
fi
