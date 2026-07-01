"""
set_s3_cors.py

Applies a CORS configuration to the emeraldbride-site S3 bucket so the
admin gallery uploader (and presigned PUT uploads) can talk to it from
the production site and local dev servers.

Run with:  python set_s3_cors.py
Requires:  AWS CLI profile 'emeraldbride' configured (region us-east-1)
"""

import json
import boto3

PROFILE = "emeraldbride"
REGION = "us-east-1"
BUCKET = "emeraldbride-site"

CORS_CONFIGURATION = {
    "CORSRules": [
        {
            "AllowedMethods": ["PUT", "GET", "HEAD"],
            "AllowedOrigins": [
                "https://d27ppwg0xrr4g5.cloudfront.net",
                "http://localhost:3000",
                "http://localhost:5500",
                "http://localhost:8000",
                "http://127.0.0.1:5500",
            ],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 3000,
        }
    ]
}


def main():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3 = session.client("s3")

    print(f"[S3] Applying CORS configuration to bucket '{BUCKET}'...")
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration=CORS_CONFIGURATION)

    print("[S3] CORS configuration applied. Fetching to confirm...")
    response = s3.get_bucket_cors(Bucket=BUCKET)
    applied_rules = response["CORSRules"]

    print()
    print("=" * 60)
    print(f" Applied CORS configuration for: {BUCKET}")
    print("=" * 60)
    print(json.dumps(applied_rules, indent=2))


if __name__ == "__main__":
    main()
