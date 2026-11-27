"""
Send a signed test delivery to the receiver, the way GitHub does.

GitHub can't reach a receiver on localhost, so use this to try the receiver
locally. It sends the same headers GitHub sends, and signs the body with
GITHUB_WEBHOOK_SECRET (read from the environment or from .env).

    python send_delivery.py deliveries/issue-opened.json
    python send_delivery.py deliveries/issue-opened.json --delivery-id <id>   # a redelivery
    python send_delivery.py deliveries/push.json --bad-signature              # expect 401
"""

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import urllib.error
import urllib.request
import uuid


def read_secret() -> str:
    if os.getenv("GITHUB_WEBHOOK_SECRET"):
        return os.environ["GITHUB_WEBHOOK_SECRET"]
    env_file = pathlib.Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "GITHUB_WEBHOOK_SECRET":
                return value.strip().strip("'\"")
    raise SystemExit("Set GITHUB_WEBHOOK_SECRET in the environment or in .env")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("file", help='A JSON file with {"event": ..., "payload": ...}')
    parser.add_argument("--url", default="http://localhost:8000/webhooks/github")
    parser.add_argument("--delivery-id", help="Reuse a delivery ID, as a GitHub redelivery does")
    parser.add_argument("--bad-signature", action="store_true", help="Sign with the wrong secret")
    args = parser.parse_args()

    fixture = json.loads(pathlib.Path(args.file).read_text())
    body = json.dumps(fixture["payload"]).encode()
    secret = b"wrong-secret" if args.bad_signature else read_secret().encode()
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    delivery_id = args.delivery_id or str(uuid.uuid4())

    request = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "GitHub-Hookshot/send-delivery",
            "X-GitHub-Event": fixture["event"],
            "X-GitHub-Delivery": delivery_id,
            "X-Hub-Signature-256": signature,
        },
    )
    print(f"delivery {delivery_id} ({fixture['event']})")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.status, response.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())
    except urllib.error.URLError as e:
        raise SystemExit(f"Couldn't reach {args.url}: {e.reason}")


if __name__ == "__main__":
    main()
