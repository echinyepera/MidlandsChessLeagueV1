from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def staff_required(view):
    """Admin/Arbiter console: logged-in staff only."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, "That area is for League Admins only.")
            return redirect("league:home")
        return view(request, *args, **kwargs)
    return wrapper


def captain_required(view):
    """Captain portal: logged-in user with a CaptainProfile; forces a password change after the temp password."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        profile = getattr(request.user, "captain_profile", None)
        if profile is None:
            messages.error(request, "This account is not linked to a club.")
            return redirect("league:home")
        request.captain = profile
        return view(request, *args, **kwargs)
    return wrapper
