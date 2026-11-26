from django.conf import settings

from .models import SiteInfo
from .services import transfer_window_open


def league_globals(request):
    return {
        "SEASON": settings.LEAGUE_SEASON,
        "site_info": SiteInfo.load(),
        "transfer_window_open": transfer_window_open(),
    }
