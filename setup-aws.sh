#!/usr/bin/env bash
set -euo pipefail

BUCKET_NAME="emeraldbride-site"
REGION="us-east-1"
PROFILE="emeraldbride"
OAC_NAME="emeraldbride-oac"
COMMENT="Emerald Bride site"

trap 'echo "Error: command failed: $BASH_COMMAND" >&2; exit 1' ERR

aws s3api create-bucket \
  --bucket "$BUCKET_NAME" \
  --region "$REGION" \
  --profile "$PROFILE"

aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}' \
  --region "$REGION" \
  --profile "$PROFILE"

aws s3api put-bucket-versioning \
  --bucket "$BUCKET_NAME" \
  --versioning-configuration Status=Enabled \
  --region "$REGION" \
  --profile "$PROFILE"

OAC_ID=$(aws cloudfront create-origin-access-control \
  --origin-access-control-config '{"Name":"'"$OAC_NAME"'","OriginAccessControlOriginType":"s3","SigningBehavior":"always","SigningProtocol":"sigv4","Description":"Origin access control for Emerald Bride site"}' \
  --query 'OriginAccessControl.Id' \
  --output text \
  --profile "$PROFILE")

CALLER_REFERENCE="$(date +%s)"
TMP_JSON="$(mktemp)"
cleanup() {
  rm -f "$TMP_JSON"
}
trap 'cleanup; echo "Error: command failed: $BASH_COMMAND" >&2; exit 1' ERR

cat > "$TMP_JSON" <<EOF
{
  "CallerReference": "$CALLER_REFERENCE",
  "Comment": "$COMMENT",
  "Enabled": true,
  "PriceClass": "PriceClass_100",
  "DefaultRootObject": "index.html",
  "CustomErrorResponses": {
    "Quantity": 1,
    "Items": [
      {
        "ErrorCode": 404,
        "ResponsePagePath": "/index.html",
        "ResponseCode": "200",
        "ErrorCachingMinTTL": 300
      }
    ]
  },
  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "S3-$BUCKET_NAME",
        "DomainName": "$BUCKET_NAME.s3.$REGION.amazonaws.com",
        "S3OriginConfig": {
          "OriginAccessIdentity": ""
        },
        "OriginAccessControlId": "$OAC_ID"
      }
    ]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "S3-$BUCKET_NAME",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 2,
      "Items": ["GET", "HEAD"],
      "CachedMethods": {
        "Quantity": 2,
        "Items": ["GET", "HEAD"]
      }
    },
    "Compress": true,
    "ForwardedValues": {
      "QueryString": false,
      "Cookies": {
        "Forward": "none"
      }
    },
    "MinTTL": 0,
    "DefaultTTL": 86400,
    "MaxTTL": 31536000
  },
  "ViewerCertificate": {
    "CloudFrontDefaultCertificate": true
  },
  "Restrictions": {
    "GeoRestriction": {
      "RestrictionType": "none",
      "Items": [],
      "Quantity": 0
    }
  },
  "Aliases": {
    "Quantity": 0,
    "Items": []
  }
}
EOF

DIST_ID=$(aws cloudfront create-distribution \
  --distribution-config "file://$TMP_JSON" \
  --query 'Distribution.Id' \
  --output text \
  --profile "$PROFILE")

ACCOUNT_ID=$(aws sts get-caller-identity \
  --query Account \
  --output text \
  --profile "$PROFILE")

POLICY_JSON="$(mktemp)"
cat > "$POLICY_JSON" <<EOF
{
  "Version": "2008-10-17",
  "Id": "PolicyForCloudFrontPrivateContent",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipalReadOnly",
      "Effect": "Allow",
      "Principal": {
        "Service": "cloudfront.amazonaws.com"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::$BUCKET_NAME/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::$ACCOUNT_ID:distribution/$DIST_ID"
        }
      }
    }
  ]
}
EOF

aws s3api put-bucket-policy \
  --bucket "$BUCKET_NAME" \
  --policy "file://$POLICY_JSON" \
  --region "$REGION" \
  --profile "$PROFILE"

DIST_DOMAIN=$(aws cloudfront get-distribution \
  --id "$DIST_ID" \
  --query 'Distribution.DomainName' \
  --output text \
  --profile "$PROFILE")

cleanup
rm -f "$POLICY_JSON"

echo "S3 Bucket Name: $BUCKET_NAME"
echo "CloudFront Distribution ID: $DIST_ID"
echo "CloudFront Domain Name: $DIST_DOMAIN"
