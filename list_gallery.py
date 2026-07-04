import json
import boto3
from botocore.exceptions import ClientError

BUCKET = "emeraldbride-site"
PREFIX = "images/gallery/"
CLOUDFRONT_BASE = "https://d27ppwg0xrr4g5.cloudfront.net"
ALLOWED_ORIGINS = {
    "https://d27ppwg0xrr4g5.cloudfront.net",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
}


def get_cors_origin(event):
    origin = (event.get("headers") or {}).get("origin", "")
    if origin in ALLOWED_ORIGINS:
        return origin
    return "https://d27ppwg0xrr4g5.cloudfront.net"


def lambda_handler(event, context):
    cors_origin = get_cors_origin(event)
    cors_headers = {
        "Access-Control-Allow-Origin": cors_origin,
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    s3 = boto3.client("s3", region_name="us-east-1")
    results = []
    paginator = s3.get_paginator("list_objects_v2")

    try:
        for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                filename = key[len(PREFIX):]
                results.append({
                    "filename": filename,
                    "url": f"{CLOUDFRONT_BASE}/{key}",
                    "size": obj["Size"],
                    "lastModified": obj["LastModified"].strftime("%Y-%m-%dT%H:%M:%S"),
                })
    except ClientError as e:
        return {
            "statusCode": 500,
            "headers": cors_headers,
            "body": json.dumps({"error": str(e)}),
        }

    return {
        "statusCode": 200,
        "headers": {**cors_headers, "Content-Type": "application/json"},
        "body": json.dumps(results),
    }
