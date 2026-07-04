"""
deploy_upload_lambda.py

Sets up two serverless endpoints for the emeraldbride site:
  1. GET  /presign-upload      → emeraldbride-presign-upload Lambda
  2. POST /save-gallery-state  → emeraldbride-save-gallery-state Lambda

Both share the IAM role emeraldbride-lambda-role and the API Gateway
HTTP API emeraldbride-api (ID: sg0k9b4ggd), prod stage.

Run with:  python deploy_upload_lambda.py
Requires:  AWS CLI profile 'emeraldbride' configured (region us-east-1)
"""

import io
import json
import time
import zipfile
import boto3
from botocore.exceptions import ClientError

PROFILE  = "emeraldbride"
REGION   = "us-east-1"
ROLE_NAME = "emeraldbride-lambda-role"
ROLE_ARN  = "arn:aws:iam::078232195170:role/emeraldbride-lambda-role"
BUCKET   = "emeraldbride-site"
API_NAME = "emeraldbride-api"
API_ID   = "sg0k9b4ggd"
STAGE_NAME = "prod"

FUNCTION_NAME      = "emeraldbride-presign-upload"
LAMBDA_FILE        = "lambda_function.py"

SAVE_STATE_FUNCTION_NAME = "emeraldbride-save-gallery-state"
SAVE_STATE_FILE          = "save_state.py"

session    = boto3.Session(profile_name=PROFILE, region_name=REGION)
iam        = session.client("iam")
aws_lambda = session.client("lambda")
apigw      = session.client("apigatewayv2")
sts        = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]


# ---------------------------------------------------------------------------
# 1. IAM Role  (inline policy covers both Lambda functions)
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
        "Resource": [
            f"arn:aws:s3:::{BUCKET}/images/gallery/*",
            f"arn:aws:s3:::{BUCKET}/gallery-state.json",
        ],
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

    # Inline S3 policy (put_role_policy is idempotent — replaces on re-run)
    try:
        iam.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName="s3-presign-gallery",
            PolicyDocument=S3_INLINE_POLICY,
        )
        print("[IAM] Inline S3 policy applied (presign-gallery + gallery-state.json).")
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
# 2. Package Lambda zips
# ---------------------------------------------------------------------------

def build_zip(source_file: str, arcname: str) -> bytes:
    print(f"[ZIP] Packaging {source_file} → {arcname}...")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(source_file, arcname=arcname)
    buf.seek(0)
    data = buf.read()
    print(f"[ZIP] Package size: {len(data):,} bytes")
    return data


# ---------------------------------------------------------------------------
# 3. Lambda helpers
# ---------------------------------------------------------------------------

def _wait_active(function_name: str) -> None:
    for _ in range(10):
        state = aws_lambda.get_function(FunctionName=function_name)["Configuration"]["State"]
        if state == "Active":
            break
        print(f"[Lambda] {function_name} state: {state}, waiting...")
        time.sleep(3)


def _create_or_update_lambda(
    function_name: str,
    handler: str,
    description: str,
    role_arn: str,
    zip_bytes: bytes,
) -> str:
    for attempt in range(6):
        try:
            fn = aws_lambda.create_function(
                FunctionName=function_name,
                Runtime="python3.12",
                Role=role_arn,
                Handler=handler,
                Code={"ZipFile": zip_bytes},
                Timeout=10,
                Description=description,
            )
            fn_arn = fn["FunctionArn"]
            print(f"[Lambda] Created {function_name}: {fn_arn}")
            break
        except aws_lambda.exceptions.ResourceConflictException:
            print(f"[Lambda] {function_name} already exists; updating code and config...")
            aws_lambda.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
            time.sleep(3)
            aws_lambda.update_function_configuration(
                FunctionName=function_name,
                Role=role_arn,
                Runtime="python3.12",
                Handler=handler,
                Timeout=10,
            )
            fn_arn = aws_lambda.get_function(FunctionName=function_name)["Configuration"]["FunctionArn"]
            print(f"[Lambda] Updated {function_name}: {fn_arn}")
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "InvalidParameterValueException" and attempt < 5:
                print(f"[Lambda] IAM role not ready yet, retrying in 5s... (attempt {attempt + 1}/6)")
                time.sleep(5)
            else:
                raise

    _wait_active(function_name)
    return fn_arn


def ensure_lambda(role_arn: str, zip_bytes: bytes) -> str:
    print(f"[Lambda] Ensuring function '{FUNCTION_NAME}'...")
    return _create_or_update_lambda(
        function_name=FUNCTION_NAME,
        handler="lambda_function.lambda_handler",
        description="Generates presigned S3 PUT URLs for emeraldbride gallery uploads",
        role_arn=role_arn,
        zip_bytes=zip_bytes,
    )


def ensure_save_state_lambda(zip_bytes: bytes) -> str:
    print(f"[Lambda] Ensuring function '{SAVE_STATE_FUNCTION_NAME}'...")
    return _create_or_update_lambda(
        function_name=SAVE_STATE_FUNCTION_NAME,
        handler="save_state.lambda_handler",
        description="Persists gallery state JSON to S3 for emeraldbride site",
        role_arn=ROLE_ARN,
        zip_bytes=zip_bytes,
    )


# ---------------------------------------------------------------------------
# 4 & 5. API Gateway HTTP API — presign-upload route
# ---------------------------------------------------------------------------

def ensure_api(fn_arn: str) -> str:
    print(f"[APIGW] Ensuring HTTP API '{API_NAME}' exists...")

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
                "AllowMethods": ["GET", "POST", "OPTIONS"],
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

    # Lambda invoke permission
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
# 6. API Gateway — save-gallery-state route
# ---------------------------------------------------------------------------

def ensure_save_state_route(fn_arn: str) -> None:
    api_id = API_ID
    print(f"[APIGW] Ensuring route POST /save-gallery-state on API {api_id}...")

    # Update CORS on the existing API to include POST
    try:
        apigw.update_api(
            ApiId=api_id,
            CorsConfiguration={
                "AllowOrigins": [
                    "https://d27ppwg0xrr4g5.cloudfront.net",
                    "http://localhost:3000",
                    "http://localhost:5500",
                    "http://localhost:8000",
                    "http://127.0.0.1:5500",
                ],
                "AllowMethods": ["GET", "POST", "OPTIONS"],
                "AllowHeaders": ["Content-Type"],
                "MaxAge": 300,
            },
        )
        print("[APIGW] CORS updated to include POST.")
    except ClientError as e:
        print(f"[APIGW] Warning updating CORS: {e}")

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
    existing_route = next(
        (r for r in routes if r.get("RouteKey") == "POST /save-gallery-state"), None
    )

    if existing_route is None:
        apigw.create_route(
            ApiId=api_id,
            RouteKey="POST /save-gallery-state",
            Target=f"integrations/{integration_id}",
        )
        print("[APIGW] Created route: POST /save-gallery-state")
    else:
        print("[APIGW] Route already exists: POST /save-gallery-state")

    # Lambda invoke permission
    source_arn = f"arn:aws:execute-api:{REGION}:{ACCOUNT_ID}:{api_id}/*/*/save-gallery-state"
    try:
        aws_lambda.add_permission(
            FunctionName=SAVE_STATE_FUNCTION_NAME,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
        print("[Lambda] Added API Gateway invoke permission for save-gallery-state.")
    except aws_lambda.exceptions.ResourceConflictException:
        print("[Lambda] Invoke permission already exists for save-gallery-state.")


# ---------------------------------------------------------------------------
# 7. Deploy stage
# ---------------------------------------------------------------------------

def ensure_stage(api_id: str) -> None:
    print(f"[APIGW] Ensuring stage '{STAGE_NAME}'...")
    stages = apigw.get_stages(ApiId=api_id).get("Items", [])
    existing = next((s for s in stages if s["StageName"] == STAGE_NAME), None)

    if existing is None:
        apigw.create_stage(ApiId=api_id, StageName=STAGE_NAME, AutoDeploy=True)
        print(f"[APIGW] Created and deployed stage '{STAGE_NAME}'.")
    else:
        apigw.update_stage(ApiId=api_id, StageName=STAGE_NAME, AutoDeploy=True)
        print(f"[APIGW] Stage '{STAGE_NAME}' redeployed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(" emeraldbride infrastructure setup")
    print("=" * 60)

    # IAM role (updates inline policy to cover both functions)
    role_arn = ensure_iam_role()

    # presign-upload Lambda + API route
    presign_zip = build_zip(LAMBDA_FILE, arcname="lambda_function.py")
    fn_arn      = ensure_lambda(role_arn, presign_zip)
    api_id      = ensure_api(fn_arn)

    # save-gallery-state Lambda + API route
    save_zip        = build_zip(SAVE_STATE_FILE, arcname="save_state.py")
    save_fn_arn     = ensure_save_state_lambda(save_zip)
    ensure_save_state_route(save_fn_arn)

    # Redeploy prod stage
    ensure_stage(api_id)

    base_url = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE_NAME}"
    print()
    print("=" * 60)
    print(" Done!")
    print(f" Presign upload URL : {base_url}/presign-upload")
    print(f" Save gallery URL   : {base_url}/save-gallery-state")
    print("=" * 60)


if __name__ == "__main__":
    main()
