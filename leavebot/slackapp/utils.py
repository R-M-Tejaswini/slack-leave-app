# leavebot/slackapp/utils.py
import os
import hmac
import hashlib
import time
import logging
from django.http import HttpResponseForbidden

logger = logging.getLogger('slackapp')

def verify_slack_request(request):
    """
    Verify that the request is coming from Slack by validating the signature
    
    Args:
        request: Django HTTP request object
        
    Returns:
        bool: True if the request is valid, False otherwise
    """
    try:
        # Get the timestamp from headers
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        if not timestamp:
            logger.warning("No timestamp in Slack request")
            return False
        
        # Check if request is too old (replay attack prevention)
        if abs(time.time() - int(timestamp)) > 60 * 5:  # 5 minutes
            logger.warning("Slack request timestamp too old")
            return False
        
        # Get the signing secret from environment
        signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        if not signing_secret:
            logger.error("SLACK_SIGNING_SECRET not found in environment")
            return False
        
        # Create the signature base string
        sig_basestring = f"v0:{timestamp}:{request.body.decode()}"
        
        # Generate our signature
        my_signature = "v0=" + hmac.new(
            signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Get Slack's signature from headers
        slack_signature = request.headers.get("X-Slack-Signature", "")
        
        # Compare signatures using constant-time comparison
        is_valid = hmac.compare_digest(my_signature, slack_signature)
        
        if not is_valid:
            logger.warning("Slack signature verification failed")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Error verifying Slack request: {str(e)}")
        return False


