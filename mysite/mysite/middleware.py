from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.exceptions import SessionInterrupted


class SafeSessionMiddleware(SessionMiddleware):
    """
    Avoid crashing the request when a session is deleted mid-flight
    (for example, logout in another tab). In that case, just return the response.
    """

    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            return response
