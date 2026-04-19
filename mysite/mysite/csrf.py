from django.http import JsonResponse
from django.shortcuts import render


def csrf_failure(request, reason="", template_name="403_csrf.html"):
    wants_json = (
        request.path.startswith("/api/")
        or "/api/" in request.path
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )
    payload = {
        "success": False,
        "error": "CSRF verification failed.",
        "reason": reason,
    }
    if wants_json:
        return JsonResponse(payload, status=403)
    return render(request, template_name, payload, status=403)
