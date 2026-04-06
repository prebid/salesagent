"""Mock creative agent HTTP sidecar for E2E testing.

Serves a controllable format catalog that BDD tests configure
before each scenario via the /test/* endpoints.

Endpoints:
    GET  /formats          -- returns the current format catalog (JSON array)
    POST /test/set-formats -- replaces the format catalog with request body
    POST /test/reset       -- clears catalog to empty list
    GET  /health           -- returns 200 (Docker health check)
"""

import json
import os

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# Global mutable state: the current format catalog.
_formats: list[dict] = []


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/formats")
def get_formats():
    return jsonify(_formats)


@app.route("/test/set-formats", methods=["POST"])
def set_formats():
    global _formats
    data = request.get_json(force=True)
    if not isinstance(data, list):
        return Response(
            json.dumps({"error": "body must be a JSON array"}),
            status=400,
            content_type="application/json",
        )
    _formats = data
    return jsonify({"status": "ok", "count": len(_formats)})


@app.route("/test/reset", methods=["POST"])
def reset_formats():
    global _formats
    _formats = []
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    app.run(host="0.0.0.0", port=port)
