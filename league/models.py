"""Midlands Chess League - database schema (the 'skeleton').

Points rules (LOCKED):
  Board points : win 1 / draw 0.5 / loss 0, over 7 boards (max 7 per fixture).
  Team points  : fixture win 3 / draw 1 each / loss 0.
Both are COMPUTED from MatchResult rows - never typed in by hand.
Home club plays White on every board (as in the approved prototype).
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .constants import (BOARDS, DEFAULT_LINEUP_DEADLINE_HOURS, DRAW_TEAM_POINTS, LINEUP_DEADLINE_KEY,
                        WIN_TEAM_POINTS)


class Club(models.Model):
    name = models.CharField(max_length=60, unique=True)
    logo = models.ImageField(upload_to="club_logos/", blank=True)
    logo_url = models.URLField(blank=True, help_text="Optional. Easiest option on free hosting.")
    date_joined = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def logo_src(self):
        if self.logo:
            return self.logo.url
        return self.logo_url or ""

    @property
    def initials(self):
        return "".join(w[0] for w in self.name.split()[:2]).upper()

    @property
    def captain_profile(self):
        return self.captains.select_related("user").first()


class Player(models.Model):
    class Gender(models.TextChoices):
        MALE = "Male", "Male"
        FEMALE = "Female", "Female"
        OTHER = "Other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "Active", "Active"
        TRANSFERRED = "Transferred", "Transferred"  # moved clubs this season; still eligible for his/her new club

    first_name = models.CharField(max_length=40)
    last_name = models.CharField(max_length=40)
    dob = models.DateField("date of birth")
    gender = models.CharField(max_length=10, choices=Gender.choices)
    fide_id = models.CharField("FIDE / ZCF ID", max_length=20, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    club = models.ForeignKey(Club, on_delete=models.PROTECT, related_name="players")
    elo_rating = models.PositiveIntegerField(default=1200, validators=[MaxValueValidator(3500)])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-elo_rating", "last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["fide_id"], condition=~Q(fide_id=""), name="unique_nonblank_fide_id"),
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        t = timezone.localdate()
        return t.year - self.dob.year - ((t.month, t.day) < (self.dob.month, self.dob.day))

    def clean(self):
        if self.dob and (self.dob > timezone.localdate() or self.dob.year < 1900):
            raise ValidationError({"dob": "Date of birth must be between 1900 and today."})


class Fixture(models.Model):
    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        COMPLETED = "Completed", "Completed"

    home_club = models.ForeignKey(Club, on_delete=models.PROTECT, related_name="home_fixtures")
    away_club = models.ForeignKey(Club, on_delete=models.PROTECT, related_name="away_fixtures")
    venue = models.CharField(max_length=100, blank=True)
    match_date = models.DateTimeField(null=True, blank=True, help_text="Blank = TBD. Several fixtures may share a date/time.")
    round_number = models.PositiveIntegerField()
    is_catchup = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = [F("match_date").asc(nulls_last=True), "round_number", "id"]
        constraints = [
            models.CheckConstraint(condition=~Q(home_club=F("away_club")), name="fixture_clubs_differ"),
        ]

    def __str__(self):
        return f"R{self.round_number}: {self.home_club} vs {self.away_club}"

    # ---- computed points (never stored, so they can't drift out of sync) ----
    @property
    def result_rows(self):
        return list(self.results.all())  # uses prefetch_related("results") when available

    @property
    def is_complete(self):
        return len(self.result_rows) == BOARDS

    @property
    def home_board_points(self):
        return sum((r.white_points for r in self.result_rows), Decimal("0"))

    @property
    def away_board_points(self):
        return sum((r.black_points for r in self.result_rows), Decimal("0"))

    @property
    def home_team_points(self):
        return self._team_points(self.home_board_points, self.away_board_points)

    @property
    def away_team_points(self):
        return self._team_points(self.away_board_points, self.home_board_points)

    def _team_points(self, mine, theirs):
        if not self.is_complete:
            return None
        if mine > theirs:
            return WIN_TEAM_POINTS
        if mine == theirs:
            return DRAW_TEAM_POINTS
        return 0

    # ---- lineup helpers ----
    def lineup_for(self, club):
        club_id = getattr(club, "pk", club)
        return next((l for l in self.lineups.all() if l.club_id == club_id), None)

    @property
    def home_lineup_in(self):
        return self.lineup_for(self.home_club_id) is not None

    @property
    def away_lineup_in(self):
        return self.lineup_for(self.away_club_id) is not None

    @property
    def lineup_deadline(self):
        if not self.match_date:
            return None
        return self.match_date - timedelta(hours=lineup_deadline_hours())

    def refresh_status(self):
        """Called by the MatchResult signal: Completed only when all 7 boards are recorded."""
        new = self.Status.COMPLETED if self.results.count() == BOARDS else self.Status.PENDING
        if new != self.status:
            self.status = new
            self.save(update_fields=["status"])


class Lineup(models.Model):
    """A captain's submitted 7-board line-up. Locked after submission; only the Admin can unlock."""
    fixture = models.ForeignKey(Fixture, on_delete=models.CASCADE, related_name="lineups")
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="lineups")
    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    submitted_at = models.DateTimeField(default=timezone.now)
    locked = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fixture", "club"], name="one_lineup_per_club_per_fixture")]

    def __str__(self):
        return f"{self.club} lineup for {self.fixture}"


class LineupEntry(models.Model):
    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="entries")
    board_number = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(BOARDS)])
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="lineup_entries")

    class Meta:
        ordering = ["board_number"]
        constraints = [
            models.UniqueConstraint(fields=["lineup", "board_number"], name="unique_board_in_lineup"),
            models.UniqueConstraint(fields=["lineup", "player"], name="unique_player_in_lineup"),
        ]


class MatchResult(models.Model):
    class Result(models.TextChoices):
        WHITE = "1-0", "White Win"
        BLACK = "0-1", "Black Win"
        DRAW = "0.5-0.5", "Draw"

    fixture = models.ForeignKey(Fixture, on_delete=models.CASCADE, related_name="results")
    board_number = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(BOARDS)])
    white_player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="white_results")
    black_player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="black_results")
    result = models.CharField(max_length=7, choices=Result.choices)

    class Meta:
        ordering = ["board_number"]
        constraints = [
            models.UniqueConstraint(fields=["fixture", "board_number"], name="one_result_per_board"),
        ]

    def __str__(self):
        return f"{self.fixture} board {self.board_number}: {self.result}"

    @property
    def white_points(self):
        return {"1-0": Decimal("1"), "0-1": Decimal("0"), "0.5-0.5": Decimal("0.5")}[self.result]

    @property
    def black_points(self):
        return Decimal("1") - self.white_points


class SystemSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.key} = {self.value}"

    @classmethod
    def get(cls, key, default=""):
        return cls.objects.filter(key=key).values_list("value", flat=True).first() or default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={"value": str(value)})


def lineup_deadline_hours():
    try:
        return int(SystemSetting.get(LINEUP_DEADLINE_KEY, DEFAULT_LINEUP_DEADLINE_HOURS))
    except ValueError:
        return DEFAULT_LINEUP_DEADLINE_HOURS


class CaptainProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="captain_profile")
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="captains")
    must_change_password = models.BooleanField(default=True)  # temp password issued by Admin

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.club}"


class Transfer(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="transfers")
    from_club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="transfers_out")
    to_club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="transfers_in")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class Notice(models.Model):
    title = models.CharField(max_length=100)
    body = models.TextField(max_length=1000)
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Official(models.Model):
    class Kind(models.TextChoices):
        OFFICIAL = "official", "Leadership"
        DEVELOPER = "developer", "Developer"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OFFICIAL)
    role = models.CharField("title", max_length=40)
    name = models.CharField(max_length=60)
    photo_url = models.URLField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.role}: {self.name}"


SPONSOR_TITLE = "Partner With Us: Sponsors & Investors Wanted"
SPONSOR_MESSAGE = (
    "The Midlands Chess League is growing chess across the Midlands: more clubs, more players and more matches every season. "
    "We invite businesses, organisations and individuals to join us as sponsors and investors. Your support can fund "
    "tournaments, prizes, venues, equipment, junior development and our online league platform, while putting your brand "
    "in front of a passionate and growing community of players, families and fans. Let's make the next move together."
)


class SiteInfo(models.Model):
    """Singleton: vision + contact links shown on the public site. Starts empty - the Admin fills it in."""
    vision = models.TextField(max_length=600, blank=True)
    whatsapp = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    sponsor_title = models.CharField("sponsorship heading", max_length=80, default=SPONSOR_TITLE)
    sponsor_message = models.TextField("sponsorship message", max_length=900, default=SPONSOR_MESSAGE,
                                       help_text="Shown on the public site. The buttons use the WhatsApp and email above.")

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def whatsapp_digits(self):
        return "".join(ch for ch in self.whatsapp if ch.isdigit())


# ---------------- Phase 2 hooks (empty structures; no club/fixture links) ----------------
class IndividualTournament(models.Model):
    class Status(models.TextChoices):
        UPCOMING = "Upcoming", "Upcoming"
        ONGOING = "Ongoing", "Ongoing"
        COMPLETED = "Completed", "Completed"

    name = models.CharField(max_length=100)
    date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPCOMING)

    def __str__(self):
        return self.name


class IndividualMatch(models.Model):
    tournament = models.ForeignKey(IndividualTournament, on_delete=models.CASCADE, related_name="matches")
    round_number = models.PositiveIntegerField()
    white_player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="+")
    black_player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="+")
    result = models.CharField(max_length=7, choices=MatchResult.Result.choices, blank=True)
