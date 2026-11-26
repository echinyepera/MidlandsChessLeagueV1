"""Auto-Point Calculation: any time a board result is saved or deleted, re-evaluate the fixture.
Board/team points are computed properties, so the only stored state to keep in sync is Fixture.status."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Fixture, MatchResult


@receiver([post_save, post_delete], sender=MatchResult)
def refresh_fixture_status(sender, instance, **kwargs):
    fixture = Fixture.objects.filter(pk=instance.fixture_id).first()  # None if the fixture itself was just deleted
    if fixture:
        fixture.refresh_status()
