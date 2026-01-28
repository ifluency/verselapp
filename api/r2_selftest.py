import os
import json
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import boto3
from botocore.config import Config


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            token = (q.get("token", [""])[0] or "").strip()
            expected = (os.environ.get("R2_SELFTEST_TOKEN") or "").strip()
            if not expected or token != expected:
                return _send_json(self, 403, {"error": "Forbidden"})

            access_key = os.environ.get("R2_ACCESS_KEY_ID")
            secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
            bucket = os.environ.get("R2_BUCKET")
            endpoint = os.environ.get("R2_ENDPOINT")
            region = os.environ.get("R2_REGION", "auto")
            prefix = os.environ.get("R2_PREFIX", "precos").strip().strip("/")
            expires = int(os.environ.get("R2_PRESIGN_EXPIRES", "3600"))

            missing = [k for k, v in {
                "R2_ACCESS_KEY_ID": access_key,
                "R2_SECRET_ACCESS_KEY": secret_key,
                "R2_BUCKET": bucket,
                "R2_ENDPOINT": endpoint,
            }.items() if not v]
            if missing:
                return _send_json(self, 500, {"error": "Missing env vars", "missing": missing})

            s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,  # R2 endpoint S3
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version="s3v4"),
            )

            test_key = f"{prefix}/_selftest/{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt"
            test_body = b"hello r2 selftest"

            # 1) List before
            before = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/_selftest/")
            before_keys = [o["Key"] for o in (before.get("Contents") or [])]

            # 2) Upload
            s3.put_object(Bucket=bucket, Key=test_key, Body=test_body, ContentType="text/plain")

            # 3) List after
            after = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/_selftest/")
            after_keys = [o["Key"] for o in (after.get("Contents") or [])]

            # 4) Presigned GET
            presigned = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": test_key},
                ExpiresIn=expires,
            )

            return _send_json(self, 200, {
                "bucket": bucket,
                "endpoint": endpoint,
                "uploaded_key": test_key,
                "list_before_count": len(before_keys),
                "list_after_count": len(after_keys),
                "presigned_get_url": presigned,
            })

        except Exception as e:
            return _send_json(self, 500, {
                "error": str(e),
                "trace": traceback.format_exc(),
            })
