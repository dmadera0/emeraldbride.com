"""
deploy_upload_lambda.py

Sets up a presigned-URL endpoint for S3 uploads:
  IAM role → Lambda function → API Gateway HTTP API → /prod/presign-upload

Run with:  python deploy_upload_lambda.py
Requires:  AWS CLI profile 'emeraldbride' configured (region us-east-1)
"""

import io
import json
import time
import zipfile
import boto3
from botocore.exceptions import ClientError

PROFILE = "emeraldbride"
REGION = "us-east-1"
ROLE_NAME = "emeraldbride-lambda-role"
FUNCTION_NAME = "emeraldbride-presign-upload"
API_NAME = "emeraldbride-api"
STAGE_NAME = "prod"
BUCKET = "emeraldbride-site"
LAMBDA_FILE = "lambda_function.py"
ALLOWED_ORIGINS = [
    "https://d27ppwg0xrr4g5.cloudfront.net",
    "http://localhost",
    "http://localhost:*",
]

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
iam = session.client("iam")
aws_lambda = session.client("lambda")
apigw = session.client("apigatewayv2")
sts = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]


# ---------------------------------------------------------------------------
# 1. IAM Role
# ---------------------------------------------------------------------------

TRUST_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
})

S3_INLINE_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "s3:PutObject",
        "Resource": f"arn:aws:s3:::{BUCKET}/images/gallery/*",
    }],
})


def ensure_iam_role() -> str:
    print(f"[IAM] Ensuring role '{ROLE_NAME}' exists...")
    try:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=TRUST_POLICY,
            Description="Lambda execution role for emeraldbride presign-upload",
        )
        role_arn = role["Role"]["Arn"]
        print(f"[IAM] Created role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"[IAM] Role already exists: {role_arn}")

    # Inline S3 policy
    try:
        iam.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName="s3-presign-gallery",
            PolicyDocument=S3_INLINE_POLICY,
        )
        print("[IAM] Inline S3 policy attached.")
    except ClientError as e:
        print(f"[IAM] Warning attaching inline policy: {e}")

    # Managed CloudWatch Logs policy
    managed_policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    try:
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=managed_policy_arn)
        print("[IAM] AWSLambdaBasicExecutionRole attached.")
    except iam.exceptions.LimitExceededException:
        print("[IAM] AWSLambdaBasicExecutionRole already attached.")
    except ClientError as e:
        print(f"[IAM] Warning attaching managed policy: {e}")

    return role_arn


# ---------------------------------------------------------------------------
# 2. Package Lambda zip
# ---------------------------------------------------------------------------

def build_zip() -> bytes:
    print(f"[ZIP] Packaging {LAMBDA_FILE}...")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(LAMBDA_FILE, arcname="lambda_function.py")
    buf.seek(0)
    data = buf.read()
    print(f"[ZIP] Package size: {len(data):,} bytes")
    return data


# ---------------------------------------------------------------------------
# 3. Lambda function
# ---------------------------------------------------------------------------

def ensure_lambda(role_arn: str, zip_bytes: bytes) -> str:
    print(f"[Lambda] Ensuring function '{FUNCTION_NAME}' exists...")

    # IAM propagation can take a few seconds after role creation
    for attempt in range(6):
        try:
            fn = aws_lambda.create_function(
                FunctionName=FUNCTION_NAME,
                Runtime="python3.12",
                Role=role_arn,
                Handler="lambda_function.lambda_handler",
                Code={"ZipFile": zip_bytes},
                Timeout=10,
                Description="Generates presigned S3 PUT URLs for emeraldbride gallery uploads",
            )
            fn_arn = fn["FunctionArn"]
            print(f"[Lambda] Created function: {fn_arn}")
            break
        except aws_lambda.exceptions.ResourceConflictException:
            # Already exists — update code and config
            print("[Lambda] Function already exists; updating code and config...")
            aws_lambda.update_function_code(
                FunctionName=FUNCTION_NAME,
                ZipFile=zip_bytes,
            )
            time.sleep(3)
            aws_lambda.update_function_configuration(
                FunctionName=FUNCTION_NAME,
                Role=role_arn,
                Runtime="python3.12",
                Handler="lambda_function.lambda_handler",
                Timeout=10,
            )
            fn_arn = aws_lambda.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["FunctionArn"]
            print(f"[Lambda] Updated function: {fn_arn}")
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "InvalidParameterValueException" and attempt < 5:
                print(f"[Lambda] IAM role not ready yet, retrying in 5s... (attempt {attempt + 1}/6)")
                time.sleep(5)
            else:
                raise

    # Wait until active
    for _ in range(10):
        state = aws_lambda.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["State"]
        if state == "Active":
            break
        print(f"[Lambda] State: {state}, waiting...")
        time.sleep(3)

    return fn_arn


# ---------------------------------------------------------------------------
# 4 & 5. API Gateway HTTP API
# ---------------------------------------------------------------------------

def ensure_api(fn_arn: str) -> str:
    print(f"[APIGW] Ensuring HTTP API '{API_NAME}' exists...")

    # Check if it already exists
    existing_apis = apigw.get_apis().get("Items", [])
    api = next((a for a in existing_apis if a["Name"] == API_NAME), None)

    if api is None:
        api = apigw.create_api(
            Name=API_NAME,
            ProtocolType="HTTP",
            CorsConfiguration={
                "AllowOrigins": [
                    "https://d27ppwg0xrr4g5.cloudfront.net",
                    "http://localhost:3000",
                    "http://localhost:5500",
                    "http://localhost:8000",
                    "http://127.0.0.1:5500",
                ],
                "AllowMethods": ["GET", "OPTIONS"],
                "AllowHeaders": ["Content-Type"],
                "MaxAge": 300,
            },
        )
        print(f"[APIGW] Created API: {api['ApiId']}")
    else:
        print(f"[APIGW] API already exists: {api['ApiId']}")

    api_id = api["ApiId"]

    # Integration
    lambda_integration_uri = (
        f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{fn_arn}/invocations"
    )

    integrations = apigw.get_integrations(ApiId=api_id).get("Items", [])
    existing_integration = next(
        (i for i in integrations if i.get("IntegrationUri") == lambda_integration_uri), None
    )

    if existing_integration is None:
        integration = apigw.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=lambda_integration_uri,
            PayloadFormatVersion="2.0",
        )
        integration_id = integration["IntegrationId"]
        print(f"[APIGW] Created integration: {integration_id}")
    else:
        integration_id = existing_integration["IntegrationId"]
        print(f"[APIGW] Integration already exists: {integration_id}")

    # Route
    routes = apigw.get_routes(ApiId=api_id).get("Items", [])
    existing_route = next((r for r in routes if r.get("RouteKey") == "GET /presign-upload"), None)

    if existing_route is None:
        apigw.create_route(
            ApiId=api_id,
            RouteKey="GET /presign-upload",
            Target=f"integrations/{integration_id}",
        )
        print("[APIGW] Created route: GET /presign-upload")
    else:
        print("[APIGW] Route already exists: GET /presign-upload")

    # Lambda permission for APIGW to invoke the function
    source_arn = f"arn:aws:execute-api:{REGION}:{ACCOUNT_ID}:{api_id}/*/*/presign-upload"
    try:
        aws_lambda.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
        print("[Lambda] Added API Gateway invoke permission.")
    except aws_lambda.exceptions.ResourceConflictException:
        print("[Lambda] Invoke permission already exists.")

    return api_id


# ---------------------------------------------------------------------------
# 6. Deploy stage
# ---------------------------------------------------------------------------

def ensure_stage(api_id: str) -> None:
    print(f"[APIGW] Ensuring stage '{STAGE_NAME}'...")
    stages = apigw.get_stages(ApiId=api_id).get("Items", [])
    existing = next((s for s in stages if s["StageName"] == STAGE_NAME), None)

    if existing is None:
        apigw.create_stage(
            ApiId=api_id,
            StageName=STAGE_NAME,
            AutoDeploy=True,
        )
        print(f"[APIGW] Created and deployed stage '{STAGE_NAME}'.")
    else:
        apigw.update_stage(ApiId=api_id, StageName=STAGE_NAME, AutoDeploy=True)
        print(f"[APIGW] Stage '{STAGE_NAME}' already exists (auto-deploy enabled).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(" emeraldbride presign-upload infrastructure setup")
    print("=" * 60)

    role_arn = ensure_iam_role()
    zip_bytes = build_zip()
    fn_arn = ensure_lambda(role_arn, zip_bytes)
    api_id = ensure_api(fn_arn)
    ensure_stage(api_id)

    invoke_url = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE_NAME}/presign-upload"
    print()
    print("=" * 60)
    print(" Done!")
    print(f" Invoke URL: {invoke_url}")
    print("=" * 60)


if __name__ == "__main__":
    main()
