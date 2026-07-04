import json
import boto3
from botocore.exceptions import ClientError

BUCKET = "emeraldbride-site"
STATE_KEY = "hero-state.json"
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
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    body = event.get("body") or ""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {
            "statusCode": 400,
            "headers": cors_headers,
            "body": json.dumps({"error": "Invalid JSON body"}),
        }

    if not isinstance(data, dict):
        return {
            "statusCode": 400,
            "headers": cors_headers,
            "body": json.dumps({"error": "Body must be a JSON object"}),
        }

    s3 = boto3.client("s3", region_name="us-east-1")
    try:
        s3.put_object(
            Bucket=BUCKET,
            Key=STATE_KEY,
            Body=json.dumps(data),
            ContentType="application/json",
            CacheControl="no-cache, no-store",
        )
    except ClientError as e:
        return {
            "statusCode": 500,
            "headers": cors_headers,
            "body": json.dumps({"error": str(e)}),
        }

    return {
        "statusCode": 200,
        "headers": {**cors_headers, "Content-Type": "application/json"},
        "body": json.dumps({"success": True}),
    }
