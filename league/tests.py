"""Core rules: points, table, fixture generation, lineup validation.  Run: python manage.py test"""
from datetime import date

from django.test import TestCase

from .constants import BOARDS
from .forms import LineupForm
from .models import Club, Fixture, MatchResult, Player
from .services import calculate_league_table, generate_season_fixtures


def make_club(name, n=BOARDS):
    club = Club.objects.create(name=name)
    for i in range(n):
        Player.objects.create(first_name=f"{name}{i}", last_name="P", dob=date(2000, 1, 1), gender="Male",
                              club=club, elo_rating=1200 + i)
    return club


def play(fixture, results):
    hp, ap = list(fixture.home_club.players.all()), list(fixture.away_club.players.all())
    for i, r in enumerate(results, 1):
        MatchResult.objects.create(fixture=fixture, board_number=i, white_player=hp[i - 1], black_player=ap[i - 1], result=r)


class PointsTests(TestCase):
    def setUp(self):
        self.a, self.b = make_club("A"), make_club("B")
        self.f = Fixture.objects.create(home_club=self.a, away_club=self.b, round_number=1)

    def test_win_is_3_0_and_board_points_add_up(self):
        play(self.f, ["1-0"] * 4 + ["0.5-0.5"] + ["0-1"] * 2)       # 4.5 - 2.5
        f = Fixture.objects.get(pk=self.f.pk)
        self.assertEqual((float(f.home_board_points), float(f.away_board_points)), (4.5, 2.5))
        self.assertEqual((f.home_team_points, f.away_team_points), (3, 0))
        self.assertEqual(f.status, Fixture.Status.COMPLETED)
        table = calculate_league_table()
        self.assertEqual(table[0].club, self.a)
        self.assertEqual(table[0].points, 3)

    def test_draw_is_1_1(self):
        play(self.f, ["1-0"] * 3 + ["0-1"] * 3 + ["0.5-0.5"])        # 3.5 - 3.5
        f = Fixture.objects.get(pk=self.f.pk)
        self.assertEqual((f.home_team_points, f.away_team_points), (1, 1))

    def test_incomplete_fixture_stays_pending(self):
        play(self.f, ["1-0"] * 3)
        self.assertEqual(Fixture.objects.get(pk=self.f.pk).status, Fixture.Status.PENDING)


class GenerationTests(TestCase):
    def test_even_round_robin(self):
        for n in "ABCDEF":
            make_club(n, 1)
        created, byes = generate_season_fixtures()
        self.assertEqual(len(created), 15)
        self.assertEqual(byes, {})
        self.assertTrue(all(f.match_date is None for f in Fixture.objects.all()))

    def test_odd_clubs_get_byes(self):
        for n in "ABCDE":
            make_club(n, 1)
        created, byes = generate_season_fixtures()
        self.assertEqual(len(created), 10)
        self.assertEqual(len(byes), 5)      # one Bye per round


class LineupTests(TestCase):
    def test_duplicate_player_rejected(self):
        club = make_club("A", 8)
        ids = [p.pk for p in club.players.all()[:BOARDS]]
        data = {f"board_{i}": ids[i - 1] for i in range(1, BOARDS + 1)}
        self.assertTrue(LineupForm(data, club=club).is_valid())
        data["board_2"] = data["board_1"]
        self.assertFalse(LineupForm(data, club=club).is_valid())

    def test_other_clubs_players_not_offered(self):
        a, b = make_club("A"), make_club("B")
        other = b.players.first().pk
        data = {f"board_{i}": p.pk for i, p in enumerate(a.players.all(), 1)}
        data["board_1"] = other
        self.assertFalse(LineupForm(data, club=a).is_valid())


class CrosstableTests(TestCase):
    def test_cells_and_columns(self):
        a, b = make_club("A"), make_club("B")
        f = Fixture.objects.create(home_club=a, away_club=b, round_number=3)
        play(f, ["1-0"] * 4 + ["0.5-0.5"] + ["0-1"] * 2)
        t = calculate_league_table()
        self.assertEqual([r.club for r in t], [a, b])
        self.assertTrue(t[0].cells[0]["self"])
        self.assertEqual((t[0].cells[1]["text"], t[0].cells[1]["cls"]), ("4.5", "win"))
        self.assertEqual((t[1].cells[0]["text"], t[1].cells[0]["cls"]), ("2.5", "loss"))
