#!/usr/bin/env bash
set -euo pipefail

PROFILE="emeraldbride"
BUCKET="emeraldbride-site"
DISTRIBUTION_ID="ELIZMJ2A07E3Q"
CLOUDFRONT_URL="https://d27ppwg0xrr4g5.cloudfront.net"

trap 'echo "Error: command failed: $BASH_COMMAND" >&2; exit 1' ERR

aws s3 sync . "s3://$BUCKET/" \
  --profile "$PROFILE" \
  --exclude ".git/*" \
  --exclude "node_modules/*" \
  --exclude "setup-aws.sh" \
  --exclude "deploy-site.sh" \
  --exclude "upload-photos.sh" \
  --exclude "setup-output.txt" \
  --exclude "*.md" \
  --exclude "*.sh" \
  --exclude "index.html" \
  --exclude "admin.html" \
  --cache-control "max-age=31536000"

aws s3 sync . "s3://$BUCKET/" \
  --profile "$PROFILE" \
  --exclude ".git/*" \
  --exclude "node_modules/*" \
  --exclude "setup-aws.sh" \
  --exclude "deploy-site.sh" \
  --exclude "upload-photos.sh" \
  --exclude "setup-output.txt" \
  --exclude "*.md" \
  --exclude "*.sh" \
  --include "index.html" \
  --include "admin.html" \
  --cache-control "max-age=300"

aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION_ID" \
  --paths "/*" \
  --profile "$PROFILE"

echo "Deploy complete"
echo "$CLOUDFRONT_URL"
