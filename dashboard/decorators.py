from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.urls import reverse


def staff_required(view):
    """Only active staff users may open the view; everyone else goes to the dashboard login."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            return redirect_to_login(request.get_full_path(), login_url=reverse('dashboard:login'))
        return view(request, *args, **kwargs)
    return wrapper