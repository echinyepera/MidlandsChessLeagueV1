from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.db.models import F
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Fixture, Notice, Official, Player
from .services import calculate_league_table, last_completed_round


def home(request):
    tab = "results" if request.GET.get("tab") == "results" else "upcoming"
    base = (Fixture.objects.select_related("home_club", "away_club")
            .prefetch_related("lineups", "results__white_player", "results__black_player"))
    completed = list(base.filter(status=Fixture.Status.COMPLETED).order_by(F("match_date").desc(nulls_last=True), "-id"))
    upcoming = list(base.filter(status=Fixture.Status.PENDING))
    notices = list(Notice.objects.filter(published=True))
    show_all = request.GET.get("notices") == "all"
    officials = list(Official.objects.all())
    table = calculate_league_table()
    return render(request, "public/home.html", {
        "table": table,
        "last_round": last_completed_round(),
        "played_any": any(r.played for r in table),
        "n_clubs": len(table),
        "n_players": Player.objects.count(),
        "top_players": Player.objects.select_related("club").order_by("-elo_rating", "last_name")[:5],
        "tab": tab,
        "fixtures": completed if tab == "results" else upcoming,
        "n_completed": len(completed), "n_upcoming": len(upcoming),
        "n_fixtures": len(completed) + len(upcoming),
        "notices": notices if show_all else notices[:3], "notice_total": len(notices), "show_all_notices": show_all,
        "leaders": [o for o in officials if o.kind != Official.Kind.DEVELOPER],
        "developer": next((o for o in officials if o.kind == Official.Kind.DEVELOPER), None),
    })


class StaffLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


def after_login(request):
    """Send each role to its own home after sign-in."""
    u = request.user
    if not u.is_authenticated:
        return redirect("league:login")
    if u.is_staff:
        return redirect("league:console_dashboard")
    if hasattr(u, "captain_profile"):
        return redirect("league:captain_dashboard")
    return redirect("league:home")


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "Signed out.")
    return redirect("league:home")
