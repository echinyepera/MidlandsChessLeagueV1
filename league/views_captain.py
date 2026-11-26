"""Captain portal: sees only their own club. Accounts are created by the Admin (no public sign-up)."""
from django.contrib import messages
from django.contrib.auth.views import PasswordChangeView
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator

from .constants import BOARDS
from .decorators import captain_required
from .forms import LineupForm, StyledPasswordChangeForm
from .models import Fixture, Lineup, LineupEntry, Player


@captain_required
def captain_dashboard(request):
    club = request.captain.club
    mine = (Fixture.objects.filter(Q(home_club=club) | Q(away_club=club))
            .select_related("home_club", "away_club").prefetch_related("lineups__entries__player", "results"))
    nxt = next((f for f in mine if f.status == Fixture.Status.PENDING), None)  # default ordering = soonest first, TBD last
    lineup = nxt.lineup_for(club) if nxt else None
    opponent = None
    if nxt:
        opponent = nxt.away_club if nxt.home_club_id == club.pk else nxt.home_club
    recent = []
    for f in mine.filter(status=Fixture.Status.COMPLETED).order_by(F("match_date").desc(nulls_last=True), "-id")[:5]:
        home = f.home_club_id == club.pk
        me, them = (f.home_board_points, f.away_board_points) if home else (f.away_board_points, f.home_board_points)
        recent.append({"fixture": f, "opponent": f.away_club if home else f.home_club, "me": me, "them": them,
                       "outcome": "win" if me > them else "loss" if me < them else "draw"})
    return render(request, "captain/dashboard.html", {
        "club": club, "profile": request.captain, "next": nxt, "lineup": lineup, "opponent": opponent,
        "i_am_home": bool(nxt and nxt.home_club_id == club.pk),
        "opp_in": bool(nxt and nxt.lineup_for(opponent)),
        "recent": recent, "squad": club.players.all(),
    })


@captain_required
def submit_lineup(request, fixture_id):
    club = request.captain.club
    fixture = get_object_or_404(Fixture.objects.select_related("home_club", "away_club").prefetch_related("lineups"), pk=fixture_id)
    if club.pk not in (fixture.home_club_id, fixture.away_club_id):
        raise Http404  # captains can only touch their own club's fixtures
    if fixture.status == Fixture.Status.COMPLETED:
        messages.error(request, "Results are already in for that fixture.")
        return redirect("league:captain_dashboard")

    lineup = fixture.lineup_for(club)
    entries = list(lineup.entries.select_related("player")) if lineup else []
    opponent = fixture.away_club if fixture.home_club_id == club.pk else fixture.home_club
    ctx = {"club": club, "fixture": fixture, "opponent": opponent, "lineup": lineup, "entries": entries}

    if lineup and lineup.locked:                       # submitted -> read-only until the Admin unlocks it
        return render(request, "captain/lineup.html", {**ctx, "locked": True})
    if club.players.count() < BOARDS:
        messages.error(request, f"Your club needs at least {BOARDS} players to field a team. Please contact the Admin.")
        return redirect("league:captain_dashboard")

    form = LineupForm(request.POST or None, club=club, initial_map={e.board_number: e.player_id for e in entries})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            lineup, _ = Lineup.objects.update_or_create(
                fixture=fixture, club=club,
                defaults={"submitted_by": request.user, "submitted_at": timezone.now(), "locked": True})
            lineup.entries.all().delete()
            LineupEntry.objects.bulk_create(
                LineupEntry(lineup=lineup, board_number=i, player=form.cleaned_data[f"board_{i}"])
                for i in range(1, BOARDS + 1))
        messages.success(request, "Lineup submitted and locked. Good luck!")
        return redirect("league:captain_dashboard")
    return render(request, "captain/lineup.html", {**ctx, "form": form, "locked": False})


class CaptainPasswordChange(PasswordChangeView):
    template_name = "captain/password.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("league:after_login")

    @method_decorator(captain_required)
    def dispatch(self, *a, **kw):
        return super().dispatch(*a, **kw)

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.captain.must_change_password = False
        self.request.captain.save(update_fields=["must_change_password"])
        messages.success(self.request, "Password changed.")
        return response
