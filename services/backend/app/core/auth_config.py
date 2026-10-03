"""AWS Cognito configuration for JWT verification (AUTH-001)."""
import os

COGNITO_REGION = os.getenv("AWS_REGION", "us-west-2")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
COGNITO_ENDPOINT = os.getenv("COGNITO_ENDPOINT")  # For cognito-local: http://cognito-local:9229

# Build URLs - use custom endpoint for local dev, real Cognito for prod
if COGNITO_ENDPOINT:
    COGNITO_JWKS_URL = f"{COGNITO_ENDPOINT}/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    COGNITO_ISSUER = f"{COGNITO_ENDPOINT}/{COGNITO_USER_POOL_ID}"
else:
    COGNITO_JWKS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    COGNITO_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"

FRONTEND_AUDIENCE = os.getenv("COGNITO_FRONTEND_CLIENT_ID")
MCP_AUDIENCE = os.getenv("COGNITO_MCP_CLIENT_ID")
MCP_M2M_AUDIENCE = os.getenv("COGNITO_MCP_M2M_CLIENT_ID")

# Support comma-separated client IDs (e.g. multiple MCP app clients)
_raw_audiences = [FRONTEND_AUDIENCE, MCP_AUDIENCE, MCP_M2M_AUDIENCE]
ALLOWED_AUDIENCES = []
for _aud in _raw_audiences:
    if _aud:
        ALLOWED_AUDIENCES.extend(a.strip() for a in _aud.split(",") if a.strip())
