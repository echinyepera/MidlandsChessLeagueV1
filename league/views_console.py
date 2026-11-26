"""Admin / Arbiter console. Every view is staff-only. Several forms per page are told apart by a hidden `action`."""
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Max, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import get_random_string

from .constants import BOARDS
from .decorators import staff_required
from .forms import (CaptainForm, ClubForm, FixtureForm, NoticeForm, OfficialForm, PlayerForm, ResultsForm,
                    ScheduleForm, SiteInfoForm, TransferForm, TransferWindowForm)
from .models import (CaptainProfile, Club, Fixture, Lineup, MatchResult, Notice, Official, Player, SiteInfo,
                     Transfer)
from .services import (board_results_text, dashboard_stats, fixtures_text, fmt_pts, generate_catchup_fixtures,
                       generate_season_fixtures, results_text, set_transfer_window, smart_alerts,
                       transfer_window_open, transfer_window_text)

TEMP_PW_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _form_errors(request, form):
    for errs in form.errors.values():
        for e in errs:
            messages.error(request, e)


# ------------------------------------------------------------------ dashboard
@staff_required
def dashboard(request):
    return render(request, "console/dashboard.html", {
        "tab": "dashboard", "alerts": smart_alerts(), "stats": dashboard_stats(),
    })


# ------------------------------------------------------------------ clubs & rosters
@staff_required
def clubs(request):
    form = ClubForm()
    if request.method == "POST":
        form = ClubForm(request.POST, request.FILES)
        if form.is_valid():
            club = form.save()
            messages.success(request, f"{club.name} added. Starts on 0 points - use Fixtures → catch-up if the season has started.")
            return redirect("league:console_clubs")
    qs = Club.objects.annotate(n_players=Count("players")).prefetch_related("captains__user")
    return render(request, "console/clubs.html", {
        "tab": "clubs", "form": form, "clubs": qs,
        "n_players": Player.objects.count(), "n_fixtures": Fixture.objects.count(),
    })


def _make_captain(club, full_name, email):
    """Replace the club's captain with a fresh account + temporary password (returned once)."""
    for old in CaptainProfile.objects.filter(club=club).select_related("user"):
        if not old.user.is_staff:
            old.user.delete()          # cascades to the profile
        else:
            old.delete()
    first, _, last = full_name.strip().partition(" ")
    password = get_random_string(10, TEMP_PW_CHARS)
    user = User.objects.create_user(username=email, email=email, password=password, first_name=first, last_name=last)
    CaptainProfile.objects.create(user=user, club=club, must_change_password=True)
    return password


@staff_required
def club_detail(request, pk):
    club = get_object_or_404(Club, pk=pk)
    player_form, captain_form = PlayerForm(initial={"elo_rating": 1200}), CaptainForm()
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_player":
            player_form = PlayerForm(request.POST)
            if player_form.is_valid():
                p = player_form.save(commit=False)
                p.club = club
                if Player.objects.filter(club=club, first_name__iexact=p.first_name, last_name__iexact=p.last_name, dob=p.dob).exists():
                    player_form.add_error(None, "This player is already on the roster.")
                else:
                    p.save()
                    messages.success(request, f"{p.full_name} added to {club}.")
                    return redirect("league:console_club", pk=club.pk)

        elif action == "remove_player":
            p = get_object_or_404(Player, pk=request.POST.get("player"), club=club)
            if MatchResult.objects.filter(Q(white_player=p) | Q(black_player=p)).exists():
                messages.error(request, f"{p.full_name} appears in saved results and can't be removed.")
            else:
                Lineup.objects.filter(club=club, entries__player=p, fixture__status=Fixture.Status.PENDING).delete()
                p.delete()
                messages.success(request, f"{p.full_name} removed. Any submitted lineup containing them was cleared.")
            return redirect("league:console_club", pk=club.pk)

        elif action == "set_captain":
            captain_form = CaptainForm(request.POST)
            if captain_form.is_valid():
                pw = _make_captain(club, captain_form.cleaned_data["full_name"], captain_form.cleaned_data["email"])
                messages.success(request, f"Captain account created ({captain_form.cleaned_data['email']}). "
                                          f"Temporary password: {pw} - share it privately; it must be changed at first login.")
                return redirect("league:console_club", pk=club.pk)

        elif action == "reset_password":
            prof = club.captain_profile
            if prof:
                pw = get_random_string(10, TEMP_PW_CHARS)
                prof.user.set_password(pw); prof.user.save()
                prof.must_change_password = True; prof.save(update_fields=["must_change_password"])
                messages.success(request, f"New temporary password for {prof.user.email}: {pw}")
            return redirect("league:console_club", pk=club.pk)

        elif action == "remove_captain":
            for prof in CaptainProfile.objects.filter(club=club).select_related("user"):
                prof.user.delete() if not prof.user.is_staff else prof.delete()
            messages.success(request, "Captain removed.")
            return redirect("league:console_club", pk=club.pk)

        elif action == "delete_club":
            if club.players.exists():
                messages.error(request, "Move or remove this club's players before deleting it.")
            else:
                try:
                    club.delete()
                    messages.success(request, "Club deleted.")
                    return redirect("league:console_clubs")
                except ProtectedError:
                    messages.error(request, "This club has fixtures. Delete those first.")
            return redirect("league:console_club", pk=club.pk)

    players = club.players.all()
    avg = round(sum(p.elo_rating for p in players) / len(players)) if players else None
    return render(request, "console/club_detail.html", {
        "tab": "clubs", "club": club, "players": players, "avg_elo": avg, "captain": club.captain_profile,
        "player_form": player_form, "captain_form": captain_form,
    })


# ------------------------------------------------------------------ transfers & transfer window
@staff_required
def transfers(request):
    form, window_form = TransferForm(), TransferWindowForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "toggle_window":
            window_form = TransferWindowForm(request.POST)
            opening = not transfer_window_open()
            if window_form.is_valid():
                set_transfer_window(opening, window_form.cleaned_data.get("closes_on"))
                messages.success(request, f"Transfer window {'OPENED' if opening else 'CLOSED'}. Share the announcement below.")
                return redirect("league:export_transfer_window")
        elif action == "process_transfer":
            form = TransferForm(request.POST)
            if form.is_valid():
                p, to = form.cleaned_data["player"], form.cleaned_data["to_club"]
                old = p.club
                with transaction.atomic():
                    # a player who leaves is taken out of the old club's submitted lineups for upcoming matches
                    Lineup.objects.filter(club=old, entries__player=p, fixture__status=Fixture.Status.PENDING).delete()
                    p.club, p.status = to, Player.Status.TRANSFERRED
                    p.save(update_fields=["club", "status"])
                    Transfer.objects.create(player=p, from_club=old, to_club=to)
                messages.success(request, f"{p.full_name} moved from {old} to {to}.")
                return redirect("league:console_transfers")
    return render(request, "console/transfers.html", {
        "tab": "transfers", "form": form, "window_form": window_form, "window_open": transfer_window_open(),
        "history": Transfer.objects.select_related("player", "from_club", "to_club")[:50],
    })


# ------------------------------------------------------------------ fixtures
@staff_required
def fixtures(request):
    add_form = FixtureForm()
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "generate_season":
            if Fixture.objects.exists():
                messages.error(request, "Fixtures already exist. Delete them first, or use catch-up for a new club.")
            elif Club.objects.count() < 2:
                messages.error(request, "Add at least two clubs first.")
            else:
                created, byes = generate_season_fixtures(double_round=bool(request.POST.get("double")))
                bye_txt = "; ".join(f"Round {r}: {c} has a Bye" for r, c in sorted(byes.items()))
                messages.success(request, f"{len(created)} fixtures generated with dates TBD." + (f" {bye_txt}." if bye_txt else ""))
            return redirect("league:console_fixtures")

        if action == "generate_catchup":
            club = get_object_or_404(Club, pk=request.POST.get("club"))
            created = generate_catchup_fixtures(club)
            messages.success(request, f"{len(created)} catch-up fixture(s) created for {club} (dates TBD)." if created
                             else f"{club} already has a fixture against every other club.")
            return redirect("league:console_fixtures")

        if action == "add_fixture":
            add_form = FixtureForm(request.POST)
            if add_form.is_valid():
                add_form.save()
                messages.success(request, "Fixture created.")
                return redirect("league:console_fixtures")

        elif action in ("set_schedule", "set_round_schedule"):
            sf = ScheduleForm(request.POST)
            if sf.is_valid():
                if action == "set_schedule":
                    qs = Fixture.objects.filter(pk=request.POST.get("fixture"))
                else:
                    qs = Fixture.objects.filter(round_number=request.POST.get("round"))
                fields = {"match_date": sf.cleaned_data["match_date"]}
                if sf.cleaned_data["venue"] or action == "set_schedule":
                    fields["venue"] = sf.cleaned_data["venue"]
                n = qs.update(**fields)
                messages.success(request, f"Schedule saved for {n} fixture{'s' if n != 1 else ''}.")
            else:
                _form_errors(request, sf)
            return redirect("league:console_fixtures")

        elif action == "delete_fixture":
            Fixture.objects.filter(pk=request.POST.get("fixture")).delete()
            messages.success(request, "Fixture deleted.")
            return redirect("league:console_fixtures")

        elif action == "unlock_lineup":
            Lineup.objects.filter(pk=request.POST.get("lineup")).update(locked=False)
            messages.success(request, "Lineup unlocked - the captain can now edit and resubmit it.")
            return redirect("league:console_fixtures")

    qs = (Fixture.objects.select_related("home_club", "away_club").prefetch_related("lineups", "results")
          .order_by("round_number", "id"))
    rounds = []
    for rn, grp in groupby(qs, key=lambda f: f.round_number):
        grp = list(grp)
        rounds.append({"number": rn, "fixtures": grp, "catchup": any(f.is_catchup for f in grp),
                       "tbd": sum(1 for f in grp if f.match_date is None),
                       "any_complete": any(f.status == Fixture.Status.COMPLETED for f in grp)})
    return render(request, "console/fixtures.html", {
        "tab": "fixtures", "rounds": rounds, "add_form": add_form, "clubs": Club.objects.all(),
        "next_round": (Fixture.objects.aggregate(m=Max("round_number"))["m"] or 0) + 1,
    })


# ------------------------------------------------------------------ results (Arbiter)
@staff_required
def results(request):
    all_fx = Fixture.objects.select_related("home_club", "away_club").order_by("round_number", "id")
    fixture = form = None
    fx_id = request.POST.get("fixture") or request.GET.get("fixture")
    warning = ""
    if fx_id:
        fixture = get_object_or_404(Fixture.objects.select_related("home_club", "away_club")
                                    .prefetch_related("lineups__entries"), pk=fx_id)
        if fixture.home_club.players.count() < BOARDS or fixture.away_club.players.count() < BOARDS:
            warning = f"Each club needs at least {BOARDS} players on its roster."
        elif request.method == "POST":
            form = ResultsForm(request.POST, fixture=fixture)
            if form.is_valid():
                with transaction.atomic():
                    for i in range(1, BOARDS + 1):
                        MatchResult.objects.update_or_create(
                            fixture=fixture, board_number=i,
                            defaults={"white_player": form.cleaned_data[f"w{i}"], "black_player": form.cleaned_data[f"b{i}"],
                                      "result": form.cleaned_data[f"r{i}"]})
                fresh = Fixture.objects.select_related("home_club", "away_club").prefetch_related("results").get(pk=fixture.pk)
                messages.success(request, f"Saved: {fresh.home_club} {fmt_pts(fresh.home_board_points)} – "
                                          f"{fmt_pts(fresh.away_board_points)} {fresh.away_club}. League table updated "
                                          f"({fresh.home_team_points}–{fresh.away_team_points} team points).")
                return redirect(f"{request.path}?fixture={fixture.pk}")
        else:
            form = ResultsForm(fixture=fixture)
    return render(request, "console/results.html", {
        "tab": "results", "fixtures": all_fx, "fixture": fixture, "form": form, "warning": warning, "boards": BOARDS,
    })


# ------------------------------------------------------------------ notices / officials / site
@staff_required
def notices(request):
    form = NoticeForm(initial={"published": True})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_notice":
            form = NoticeForm(request.POST)
            if form.is_valid():
                n = form.save()
                messages.success(request, "Notice published." if n.published else "Notice saved as a draft.")
                return redirect("league:console_notices")
        elif action == "toggle_publish":
            n = get_object_or_404(Notice, pk=request.POST.get("notice"))
            n.published = not n.published; n.save(update_fields=["published"])
            return redirect("league:console_notices")
        elif action == "delete_notice":
            Notice.objects.filter(pk=request.POST.get("notice")).delete()
            messages.success(request, "Notice deleted.")
            return redirect("league:console_notices")
    return render(request, "console/notices.html", {"tab": "notices", "form": form, "notices": Notice.objects.all()})


@staff_required
def officials(request):
    items = list(Official.objects.all())
    bound = None
    if request.method == "POST":
        o = get_object_or_404(Official, pk=request.POST.get("official"))
        f = OfficialForm(request.POST, instance=o, prefix=f"o{o.pk}")
        if f.is_valid():
            f.save()
            messages.success(request, f"{o.name} saved.")
            return redirect("league:console_officials")
        bound = (o.pk, f)
    pairs = [(o, bound[1] if bound and bound[0] == o.pk else OfficialForm(instance=o, prefix=f"o{o.pk}")) for o in items]
    return render(request, "console/officials.html", {"tab": "officials", "pairs": pairs})


@staff_required
def site_info(request):
    form = SiteInfoForm(request.POST or None, instance=SiteInfo.load())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Site info saved.")
        return redirect("league:console_site")
    return render(request, "console/site.html", {"tab": "site", "form": form})


# ------------------------------------------------------------------ WhatsApp exports (free wa.me click-to-share)
def _export(request, title, text, back, tab):
    return render(request, "console/export.html", {"tab": tab, "title": title, "text": text, "back": back})


@staff_required
def export_fixtures(request, round_number):
    return _export(request, f"Round {round_number} fixtures", fixtures_text(round_number), "league:console_fixtures", "fixtures")


@staff_required
def export_results(request, round_number):
    return _export(request, f"Round {round_number} results & table", results_text(round_number), "league:console_fixtures", "fixtures")


@staff_required
def export_board_results(request, fixture_id):
    f = get_object_or_404(Fixture.objects.select_related("home_club", "away_club").prefetch_related("results"), pk=fixture_id)
    return _export(request, f"{f.home_club} vs {f.away_club} - board results", board_results_text(f), "league:console_fixtures", "fixtures")


@staff_required
def export_transfer_window(request):
    return _export(request, "Transfer window announcement", transfer_window_text(transfer_window_open()), "league:console_transfers", "transfers")
