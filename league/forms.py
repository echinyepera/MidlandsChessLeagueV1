from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .constants import BOARDS
from .models import Club, Fixture, MatchResult, Notice, Official, Player, SiteInfo

DT_FORMAT = "%Y-%m-%dT%H:%M"


class StyledMixin:
    """Gives every widget the prototype's `.input` class so the approved CSS applies."""
    def _style(self):
        for f in self.fields.values():
            if not isinstance(f.widget, (forms.CheckboxInput, forms.HiddenInput)):
                f.widget.attrs.setdefault("class", "input")


class StyledForm(StyledMixin, forms.Form):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


class StyledModelForm(StyledMixin, forms.ModelForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


class PlayerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, p):
        return f"{p.full_name} ({p.elo_rating})"


class TransferPlayerField(forms.ModelChoiceField):
    def label_from_instance(self, p):
        return f"{p.full_name} - {p.club} (ELO {p.elo_rating})"


def dt_field(**kw):
    return forms.DateTimeField(required=False, input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"],
                               widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT_FORMAT), **kw)


# ---------------- Admin console ----------------
class ClubForm(StyledModelForm):
    class Meta:
        model = Club
        fields = ["name", "logo_url", "logo"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Club name", "autocomplete": "off"}),
                   "logo_url": forms.URLInput(attrs={"placeholder": "https://…/logo.png"})}


class PlayerForm(StyledModelForm):
    dob = forms.DateField(label="Date of birth", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    class Meta:
        model = Player
        fields = ["first_name", "last_name", "dob", "gender", "fide_id", "phone", "elo_rating"]
        labels = {"elo_rating": "Initial ELO rating"}


class CaptainForm(StyledForm):
    full_name = forms.CharField(max_length=80)
    email = forms.EmailField()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise forms.ValidationError("An account with that email already exists.")
        return email


class TransferForm(StyledForm):
    player = TransferPlayerField(queryset=Player.objects.select_related("club").order_by("club__name", "last_name"))
    to_club = forms.ModelChoiceField(queryset=Club.objects.all(), label="To club")

    def clean(self):
        data = super().clean()
        p, to = data.get("player"), data.get("to_club")
        if p and to and p.club_id == to.pk:
            raise forms.ValidationError(f"{p.full_name} already plays for {to}.")
        return data


class FixtureForm(StyledModelForm):
    match_date = dt_field()

    class Meta:
        model = Fixture
        fields = ["home_club", "away_club", "round_number", "venue", "match_date"]

    def clean(self):
        data = super().clean()
        if data.get("home_club") and data.get("home_club") == data.get("away_club"):
            raise forms.ValidationError("Home and away clubs must be different.")
        return data


class NoticeForm(StyledModelForm):
    class Meta:
        model = Notice
        fields = ["title", "body", "published"]
        widgets = {"body": forms.Textarea(attrs={"placeholder": "Write the announcement…"})}


class OfficialForm(StyledModelForm):
    class Meta:
        model = Official
        fields = ["name", "role", "photo_url"]


class SiteInfoForm(StyledModelForm):
    class Meta:
        model = SiteInfo
        fields = ["vision", "whatsapp", "email", "sponsor_title", "sponsor_message"]
        labels = {"whatsapp": "WhatsApp number", "email": "Email address"}

    def clean_whatsapp(self):
        raw = self.cleaned_data["whatsapp"].strip()
        if not raw:
            return ""
        digits = "".join(c for c in raw if c.isdigit())
        if not 9 <= len(digits) <= 15:
            raise forms.ValidationError("Enter a valid number with the country code, e.g. +263 77 123 4567.")
        if digits.startswith("0"):
            raise forms.ValidationError("Start with the country code (e.g. +263), not 0.")
        return raw


class ResultsForm(StyledForm):
    """Arbiter's results entry: 7 boards x (white player, black player, result). Pre-filled from submitted lineups."""

    def __init__(self, *args, fixture, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixture = fixture
        existing = list(fixture.results.all())
        home_qs = Player.objects.filter(Q(club=fixture.home_club) | Q(pk__in=[r.white_player_id for r in existing]))
        away_qs = Player.objects.filter(Q(club=fixture.away_club) | Q(pk__in=[r.black_player_id for r in existing]))
        by_board = {r.board_number: r for r in existing}
        hl, al = fixture.lineup_for(fixture.home_club_id), fixture.lineup_for(fixture.away_club_id)
        hl_map = {e.board_number: e.player_id for e in hl.entries.all()} if hl else {}
        al_map = {e.board_number: e.player_id for e in al.entries.all()} if al else {}
        for i in range(1, BOARDS + 1):
            r = by_board.get(i)
            self.fields[f"w{i}"] = PlayerChoiceField(queryset=home_qs.distinct(), empty_label="Select player",
                                                     initial=r.white_player_id if r else hl_map.get(i))
            self.fields[f"b{i}"] = PlayerChoiceField(queryset=away_qs.distinct(), empty_label="Select player",
                                                     initial=r.black_player_id if r else al_map.get(i))
            self.fields[f"r{i}"] = forms.ChoiceField(choices=[("", "Select result")] + list(MatchResult.Result.choices),
                                                     initial=r.result if r else "")
        self._style()

    @property
    def rows(self):
        return [{"n": i, "w": self[f"w{i}"], "b": self[f"b{i}"], "r": self[f"r{i}"]} for i in range(1, BOARDS + 1)]

    def clean(self):
        data = super().clean()
        for side, label, club in (("w", "White", self.fixture.home_club), ("b", "Black", self.fixture.away_club)):
            ids = [data[f"{side}{i}"].pk for i in range(1, BOARDS + 1) if data.get(f"{side}{i}")]
            if len(ids) != len(set(ids)):
                raise forms.ValidationError(f"{club} has a player on more than one board.")
        return data


# ---------------- Captain portal ----------------
class LineupForm(StyledForm):
    """7 dropdowns populated ONLY with the captain's own club. No ELO auto-sorting: the captain chooses the order."""

    def __init__(self, *args, club, initial_map=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Player.objects.filter(club=club)
        for i in range(1, BOARDS + 1):
            self.fields[f"board_{i}"] = PlayerChoiceField(queryset=qs, empty_label="Select player", label=f"Board {i}",
                                                          initial=(initial_map or {}).get(i))
        self._style()

    @property
    def boards(self):
        return [self[f"board_{i}"] for i in range(1, BOARDS + 1)]

    def clean(self):
        data = super().clean()
        picks = [data[f"board_{i}"].pk for i in range(1, BOARDS + 1) if data.get(f"board_{i}")]
        if len(picks) != len(set(picks)):
            raise forms.ValidationError("A player can only be on one board.")
        return data


class ScheduleForm(StyledForm):
    """Inline 'set date / venue' for a fixture or a whole round. Blank date = TBD."""
    venue = forms.CharField(max_length=100, required=False)
    match_date = dt_field()


class TransferWindowForm(StyledForm):
    closes_on = forms.DateField(required=False, label="Closes on (optional)",
                                widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.widget.attrs["class"] = "input"
