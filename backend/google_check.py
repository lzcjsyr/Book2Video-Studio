"""Check the Google Cloud login (ADC) the way an image job would use it, without generating anything.

Run as its own process: `google_check.py '{"engine": ..., "models": [...]}'` prints one JSON result line.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

LOGIN = "Run `gcloud auth application-default login` in Terminal, then check again."


def check(engine: Path, models: list[str]) -> dict:
    sys.path.insert(0, str(engine))
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError, RefreshError
    from google.auth.transport.requests import Request
    from core.infra.ai.llm_client import _resolve_google_adc_project
    from core.shared import http_status

    try:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
    except DefaultCredentialsError:
        return {"ok": False, "message": "No Google Cloud login was found on this computer. " + LOGIN}
    except RefreshError as exc:
        return {"ok": False, "message": f"The Google Cloud login was refused or has expired ({exc}). " + LOGIN}
    # The same lookup jobs use: settings, environment, then the login's own project.
    project = _resolve_google_adc_project()
    if not project:
        return {"ok": False, "message": "The Google Cloud login names no project. Run `gcloud auth application-default set-quota-project PROJECT_ID`, then check again."}
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
    result = {"ok": True, "project": project, "location": location, "models": []}

    from google import genai
    client = genai.Client(vertexai=True, project=project, location=location)
    for model in models:
        try:
            # Counting tokens is free and reaches the same model endpoint as generation.
            client.models.count_tokens(model=model, contents="connection check")
            result["models"].append({"model": model, "ok": True})
        except Exception as exc:
            status = http_status(exc)
            message = {
                404: "Not available to this project. Choose another model, such as gemini-3.1-flash-image.",
                403: "Permission denied. Enable the Vertex AI API for this project and check the account's role.",
                401: "The login was refused. " + LOGIN,
            }.get(status, f"Google could not be reached: {exc}")
            result["models"].append({"model": model, "ok": False, "message": message})
            result["ok"] = False
    if not result["ok"]:
        result["message"] = "Signed in, but a model cannot be used."
    return result


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    try:
        outcome = check(Path(request["engine"]), request["models"])
    except Exception as exc:
        outcome = {"ok": False, "message": f"The check could not run: {exc}"}
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
