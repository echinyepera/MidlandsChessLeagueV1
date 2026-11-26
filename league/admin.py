from django.contrib import admin

from .models import (CaptainProfile, Club, Fixture, IndividualMatch, IndividualTournament, Lineup, LineupEntry,
                     MatchResult, Notice, Official, Player, SiteInfo, SystemSetting, Transfer)


class MatchResultInline(admin.TabularInline):
    model = MatchResult
    extra = 0


class LineupEntryInline(admin.TabularInline):
    model = LineupEntry
    extra = 0


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ("name", "date_joined")
    search_fields = ("name",)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "club", "elo_rating", "fide_id", "status")
    list_filter = ("club", "status", "gender")
    search_fields = ("first_name", "last_name", "fide_id")


@admin.register(Fixture)
class FixtureAdmin(admin.ModelAdmin):
    list_display = ("__str__", "match_date", "venue", "status", "is_catchup")
    list_filter = ("round_number", "status", "is_catchup")
    inlines = [MatchResultInline]


@admin.register(Lineup)
class LineupAdmin(admin.ModelAdmin):
    list_display = ("fixture", "club", "submitted_at", "locked")
    list_filter = ("locked",)
    actions = ["unlock"]
    inlines = [LineupEntryInline]

    @admin.action(description="Unlock selected lineups for editing")
    def unlock(self, request, queryset):
        queryset.update(locked=False)


admin.site.register([SystemSetting, CaptainProfile, Notice, Transfer, Official, SiteInfo,
                     IndividualTournament, IndividualMatch])
