"""Business logic: league table, fixture generation, smart alerts, WhatsApp message builders."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Max
from django.utils import timezone

from .constants import BOARDS, TRANSFER_CLOSES_KEY, TRANSFER_WINDOW_KEY
from .models import (Club, Fixture, Lineup, MatchResult, Notice, Player, SiteInfo, SystemSetting,
                     lineup_deadline_hours)


def fmt_pts(n):
    """4 -> '4', Decimal('4.5') -> '4.5'"""
    n = Decimal(n)
    return str(int(n)) if n == n.to_integral() else f"{n:.1f}"


# =====================================================================================
# League table
# =====================================================================================
@dataclass
class Row:
    club: Club
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    board_for: Decimal = field(default_factory=lambda: Decimal("0"))
    board_against: Decimal = field(default_factory=lambda: Decimal("0"))
    points: int = 0   # TEAM points (3/1/0) = "MP"
    rank: int = 0
    results: dict = field(default_factory=dict)   # opponent club pk -> [(my board pts, their board pts), ...]
    cells: list = field(default_factory=list)     # crosstable cells, ordered by rank (filled by calculate_league_table)

    @property
    def board_diff(self):
        return self.board_for - self.board_against


def calculate_league_table():
    """All clubs ranked by match (team) points, then game (board) points - the President's confirmed rule."""
    rows = {c.pk: Row(club=c) for c in Club.objects.all()}
    for f in Fixture.objects.filter(status=Fixture.Status.COMPLETED).prefetch_related("results"):
        h, a = rows.get(f.home_club_id), rows.get(f.away_club_id)
        if not h or not a:
            continue
        hb, ab = f.home_board_points, f.away_board_points
        h.played += 1; a.played += 1
        h.board_for += hb; h.board_against += ab
        a.board_for += ab; a.board_against += hb
        hp, ap = f.home_team_points, f.away_team_points
        h.results.setdefault(a.club.pk, []).append((hb, ab))
        a.results.setdefault(h.club.pk, []).append((ab, hb))
        h.points += hp; a.points += ap
        if hp > ap:
            h.won += 1; a.lost += 1
        elif hp < ap:
            a.won += 1; h.lost += 1
        else:
            h.drawn += 1; a.drawn += 1

    # President's rule (confirmed): rank by match (team) points, then game (board) points. Remaining ties: alphabetical.
    final = sorted(rows.values(), key=lambda r: (-r.points, -r.board_for, r.club.name))
    for i, r in enumerate(final, 1):
        r.rank = i
    for r in final:   # crosstable: one cell per opponent, in rank order
        for opp in final:
            if opp is r:
                r.cells.append({"self": True})
                continue
            games = r.results.get(opp.club.pk, [])
            diff = sum(m - t for m, t in games)
            r.cells.append({"self": False, "text": " / ".join(fmt_pts(m) for m, _ in games),
                            "title": ", ".join(f"{fmt_pts(m)}–{fmt_pts(t)}" for m, t in games),
                            "cls": "" if not games else "win" if diff > 0 else "loss" if diff < 0 else "draw"})
    return final


def last_completed_round():
    return Fixture.objects.filter(status=Fixture.Status.COMPLETED).aggregate(m=Max("round_number"))["m"]


# =====================================================================================
# Fixture generation (Round-Robin, circle method) with automatic byes
# =====================================================================================
def _max_round():
    return Fixture.objects.aggregate(m=Max("round_number"))["m"] or 0


def generate_season_fixtures(clubs=None, double_round=False):
    """Create every pairing with match_date=None (TBD). Returns (created_fixtures, byes{round: club}).
    Odd club counts get a Bye each round (that club simply has no fixture = 0 points that round)."""
    clubs = list(clubs if clubs is not None else Club.objects.all())
    if len(clubs) < 2:
        return [], {}
    by_id = {c.pk: c for c in clubs}
    ring = [c.pk for c in clubs]
    if len(ring) % 2:
        ring.append(None)  # None = Bye
    n = len(ring)
    rounds, byes = [], {}
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ring[i], ring[n - 1 - i]
            if a is None or b is None:
                byes[r + 1] = by_id[a or b]
                continue
            if (r + i) % 2:           # alternate home/away to keep it balanced
                a, b = b, a
            pairs.append((a, b))
        rounds.append(pairs)
        ring = [ring[0], ring[-1]] + ring[1:-1]
    if double_round:
        rounds += [[(b, a) for a, b in p] for p in rounds]
        byes.update({r + (n - 1): c for r, c in list(byes.items())})

    start = _max_round()
    created = []
    for idx, pairs in enumerate(rounds, 1):
        for home, away in pairs:
            created.append(Fixture(home_club=by_id[home], away_club=by_id[away], round_number=start + idx))
    Fixture.objects.bulk_create(created)
    return created, {start + r: c for r, c in byes.items()}


def generate_catchup_fixtures(club):
    """Late entry: create the missing pairings for `club` (blank dates). They share one new round number so the
    Admin can put them all on the President's special catch-up date in one click."""
    existing = {frozenset((f.home_club_id, f.away_club_id)) for f in Fixture.objects.all()}
    rn = _max_round() + 1
    created = []
    for i, other in enumerate(Club.objects.exclude(pk=club.pk)):
        if frozenset((club.pk, other.pk)) in existing:
            continue
        home, away = (club, other) if i % 2 == 0 else (other, club)
        created.append(Fixture(home_club=home, away_club=away, round_number=rn, is_catchup=True))
    Fixture.objects.bulk_create(created)
    return created


# =====================================================================================
# Lineups, transfer window, alerts
# =====================================================================================
def missing_lineups(fixtures):
    """[(fixture, club), ...] for pending fixtures where a club has no submitted lineup."""
    out = []
    for f in fixtures:
        have = {l.club_id for l in f.lineups.all()}
        for club in (f.home_club, f.away_club):
            if club.pk not in have:
                out.append((f, club))
    return out


def transfer_window_open():
    return SystemSetting.get(TRANSFER_WINDOW_KEY, "CLOSED") == "OPEN"


def set_transfer_window(open_, closes_on=None):
    SystemSetting.set(TRANSFER_WINDOW_KEY, "OPEN" if open_ else "CLOSED")
    SystemSetting.set(TRANSFER_CLOSES_KEY, closes_on.isoformat() if (open_ and closes_on) else "")


def _streaks():
    """Current winning streak (consecutive board wins, most recent first) per player."""
    seqs = defaultdict(list)
    qs = (MatchResult.objects.filter(fixture__status=Fixture.Status.COMPLETED)
          .select_related("white_player", "black_player")
          .order_by("fixture__match_date", "fixture_id", "board_number"))
    for r in qs:
        seqs[r.white_player].append(r.white_points)
        seqs[r.black_player].append(r.black_points)
    best = None
    for player, pts in seqs.items():
        n = 0
        for p in reversed(pts):
            if p == 1:
                n += 1
            else:
                break
        if n >= 2 and (best is None or n > best[1]):
            best = (player, n)
    return best


def smart_alerts():
    """Colour-coded cards for the Admin dashboard. level: red / yellow / green."""
    now = timezone.now()
    alerts = []
    pending = Fixture.objects.filter(status=Fixture.Status.PENDING).select_related("home_club", "away_club").prefetch_related("lineups")

    # ---- RED ----
    soon = [f for f in pending if f.match_date and now <= f.match_date <= now + timedelta(hours=48)]
    miss = missing_lineups(soon)
    if miss:
        names = ", ".join(sorted({c.name for _, c in miss}))
        alerts.append(("red", f"{len(miss)} lineup(s) still missing for matches in the next 48 hours: {names}."))
    overdue = sorted({f.round_number for f in pending if f.match_date and f.match_date < now})
    for rn in overdue:
        alerts.append(("red", f"Round {rn} results are missing - league table not updated."))

    # ---- YELLOW ----
    if transfer_window_open():
        closes = SystemSetting.get(TRANSFER_CLOSES_KEY)
        extra = ""
        if closes:
            try:
                days = (date.fromisoformat(closes) - timezone.localdate()).days
                extra = f" (closes in {days} day{'s' if days != 1 else ''})" if days >= 0 else " (closing date has passed)"
            except ValueError:
                pass
        alerts.append(("yellow", f"Transfer Window is OPEN{extra}."))
    unscheduled = defaultdict(int)
    for f in pending:
        if f.match_date is None:
            unscheduled[f.round_number] += 1
    for rn in sorted(unscheduled):
        n = unscheduled[rn]
        alerts.append(("yellow", f"You have {n} unscheduled fixture{'s' if n != 1 else ''} for Round {rn}."))
    drafts = Notice.objects.filter(published=False).count()
    if drafts:
        alerts.append(("yellow", f"You have {drafts} unpublished league notice{'s' if drafts != 1 else ''}."))

    # ---- GREEN ----
    future = sorted((f for f in pending if f.match_date and f.match_date >= now), key=lambda f: f.match_date)
    if future:
        day = timezone.localtime(future[0].match_date).date()
        n = sum(1 for f in future if timezone.localtime(f.match_date).date() == day)
        alerts.append(("green", f"Next matchday is {day:%A %d %b} - {n} fixture{'s' if n != 1 else ''} scheduled."))
    streak = _streaks()
    if streak:
        alerts.append(("green", f"Hot streak: {streak[0].full_name} ({streak[0].club}) has won {streak[1]} boards in a row."))
    table = calculate_league_table()
    if len(table) >= 2 and table[0].played:
        gap = table[0].points - table[1].points
        alerts.append(("green", f"{table[0].club} lead {table[1].club} by {gap} point{'s' if gap != 1 else ''}."))
    total = Player.objects.count()
    new = Player.objects.filter(created_at__gte=now - timedelta(days=30)).count()
    if new and total > new:
        alerts.append(("green", f"Player base grew {round(new / (total - new) * 100)}% in the last 30 days ({new} new)."))
    return alerts


def dashboard_stats():
    now = timezone.now()
    upcoming = (Fixture.objects.filter(status=Fixture.Status.PENDING, match_date__gte=now)
                .select_related("home_club", "away_club").prefetch_related("lineups"))
    return {
        "clubs": Club.objects.count(),
        "players": Player.objects.count(),
        "pending_lineups": len(missing_lineups(upcoming)),
        "top3": Player.objects.select_related("club").order_by("-elo_rating", "last_name")[:3],
    }


# =====================================================================================
# WhatsApp click-to-share message builders (free wa.me flow; Admin can edit before sending)
# =====================================================================================
def _when(dt):
    if not dt:
        return "Date to be confirmed"
    return timezone.localtime(dt).strftime("%A, %d %b %Y @ %I:%M %p").replace(" 0", " ")


def _result_str(code):
    return {"1-0": "1 - 0", "0-1": "0 - 1", "0.5-0.5": "½ - ½"}[code]


def fixtures_text(round_number):
    fx = list(Fixture.objects.filter(round_number=round_number).select_related("home_club", "away_club"))
    lines = [f"🏆 MIDLANDS CHESS LEAGUE - ROUND {round_number} FIXTURES 🏆", ""]
    for f in fx:
        lines += [f"♟️ {f.home_club} vs {f.away_club}", f"📅 {_when(f.match_date)}"]
        if f.venue:
            lines.append(f"🏟️ Venue: {f.venue}")
        lines.append("")
    dated = [f for f in fx if f.match_date]
    if dated:
        first = min(dated, key=lambda f: f.match_date)
        dl = timezone.localtime(first.lineup_deadline)
        lines.append(f"Captains, please submit your lineups by {dl:%A} {dl:%I:%M %p}!".replace(" 0", " "))
    else:
        lines.append(f"Captains, please submit your lineups {lineup_deadline_hours()} hours before your match.")
    return "\n".join(lines)


def results_text(round_number):
    fx = (Fixture.objects.filter(round_number=round_number, status=Fixture.Status.COMPLETED)
          .select_related("home_club", "away_club").prefetch_related("results"))
    lines = [f"📊 MIDLANDS CHESS LEAGUE - ROUND {round_number} RESULTS 📊", ""]
    for f in fx:
        lines.append(f"✅ {f.home_club} {fmt_pts(f.home_board_points)} - {fmt_pts(f.away_board_points)} {f.away_club}")
    if not fx:
        lines.append("No completed results yet.")
    lines += ["", "🏅 LEAGUE TABLE", ""]
    for r in calculate_league_table():
        lines.append(f"{r.rank}. {r.club} - {r.points} pts (P{r.played}, BP {fmt_pts(r.board_for)})")
    return "\n".join(lines)


def board_results_text(fixture):
    lines = [f"♟️ {fixture.home_club} vs {fixture.away_club} - ROUND {fixture.round_number}", ""]
    for r in fixture.results.select_related("white_player", "black_player"):
        lines.append(f"Board {r.board_number}: ⚪ {r.white_player} vs ⚫ {r.black_player}  →  {_result_str(r.result)}")
    if fixture.is_complete:
        lines += ["", f"FINAL: {fixture.home_club} {fmt_pts(fixture.home_board_points)} - {fmt_pts(fixture.away_board_points)} {fixture.away_club}"]
    return "\n".join(lines)


def transfer_window_text(is_open):
    if is_open:
        closes = SystemSetting.get(TRANSFER_CLOSES_KEY)
        tail = f"\n📅 The window closes on {closes}." if closes else ""
        return ("🔓 MIDLANDS CHESS LEAGUE - TRANSFER WINDOW OPEN 🔓\n\nThe transfer window is now OPEN. "
                "Clubs, please send transfer requests to the League Admin." + tail)
    return "🔒 MIDLANDS CHESS LEAGUE - TRANSFER WINDOW CLOSED 🔒\n\nThe transfer window is now CLOSED. No further transfers will be processed."
