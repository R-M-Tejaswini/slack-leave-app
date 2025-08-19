# leavebot/slackapp/utils.py

"""
Security and Utility Functions for the Slack App.

This module contains utility functions, primarily focused on security aspects
of the Slack integration. The key component is a view decorator that verifies
the authenticity of all incoming requests from Slack, protecting the application
from forged or malicious requests.
"""

# Standard library imports
import hashlib
import hmac
import logging
import os
import time
from functools import wraps

# Django imports
from django.http import HttpRequest, HttpResponseForbidden

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def slack_verification_required(view_func):
    """
    A Django view decorator to verify that an incoming request is from Slack.

    This decorator implements Slack's request signing protocol to ensure that
    all requests to a decorated view are authentic. It protects against various
    threats, including forged requests and replay attacks.

    How it works:
    1.  **Timestamp Check:** It verifies the `X-Slack-Request-Timestamp` header to
        ensure the request is not older than 5 minutes, preventing replay attacks.
    2.  **Signature Generation:** It reconstructs the signature base string using
        the version, timestamp, and raw request body.
    3.  **HMAC Comparison:** It computes its own HMAC-SHA256 signature using the
        `SLACK_SIGNING_SECRET` and compares it to the `X-Slack-Signature` header
        from the request in a constant-time manner to prevent timing attacks.

    If verification fails, it returns an `HttpResponseForbidden` (403),
    immediately halting further processing. If successful, it executes the view.

    Usage:
        @slack_verification_required
        def my_slack_view(request):
            # This code will only run if the request is verified.
            ...
    """
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        """The actual wrapper that performs the verification."""
        try:
            # --- 1. Get required headers from the request ---
            slack_signature = request.headers.get("X-Slack-Signature")
            timestamp = request.headers.get("X-Slack-Request-Timestamp")

            if not slack_signature or not timestamp:
                logger.warning("Missing Slack signature or timestamp headers.")
                return HttpResponseForbidden("Missing required Slack headers.")

            # --- 2. Check if the request is too old (prevents replay attacks) ---
            if abs(time.time() - int(timestamp)) > 60 * 5:  # 5-minute tolerance
                logger.warning("Slack request timestamp is too old.")
                return HttpResponseForbidden("Request timestamp is too old.")

            # --- 3. Retrieve the signing secret from environment variables ---
            signing_secret = os.getenv("SLACK_SIGNING_SECRET")
            if not signing_secret:
                # This is a critical configuration error.
                logger.error("SLACK_SIGNING_SECRET environment variable is not set.")
                # We return Forbidden to the outside world but log an error internally.
                return HttpResponseForbidden("Server configuration error.")

            # --- 4. Construct the signature base string ---
            # The format is critical: "v0:{timestamp}:{request_body}"
            # The request body must be the raw, unparsed byte string.
            sig_basestring = f"v0:{timestamp}:{request.body.decode('utf-8')}"

            # --- 5. Compute our own signature using the secret ---
            my_signature = "v0=" + hmac.new(
                key=signing_secret.encode('utf-8'),
                msg=sig_basestring.encode('utf-8'),
                digestmod=hashlib.sha256
            ).hexdigest()

            # --- 6. Compare our signature with Slack's signature ---
            # It is crucial to use hmac.compare_digest() for a constant-time
            # comparison to protect against timing attacks.
            if hmac.compare_digest(my_signature, slack_signature):
                # If signatures match, the request is authentic. Proceed to the view.
                return view_func(request, *args, **kwargs)
            else:
                logger.warning("Slack signature verification failed. Mismatch.")
                return HttpResponseForbidden("Slack signature verification failed.")

        except (ValueError, TypeError) as e:
            logger.error(f"Error during Slack verification: {e}")
            return HttpResponseForbidden("Invalid request format.")

    return wrapper