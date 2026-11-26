from django.core.management.base import BaseCommand

from league.constants import TRANSFER_WINDOW_KEY
from league.models import Official, SiteInfo, SystemSetting


class Command(BaseCommand):
    help = "Create the default site info, leadership entries and settings (safe to run repeatedly)."

    def handle(self, *args, **opts):
        SiteInfo.load()
        if not Official.objects.exists():
            rows = [("official", "Chairperson", "Dr V. Manengamambo"), ("official", "Vice Chairperson", "Eng S. Bosha"),
                    ("official", "Chief Arbiter", "Mr R. Mhandu"), ("official", "Tournament Director", "Mrs H. Khuphe"),
                    ("developer", "Platform Developer", "Mr E. Chinyepera")]
            for i, (kind, role, name) in enumerate(rows):
                Official.objects.create(kind=kind, role=role, name=name, order=i)
        SystemSetting.objects.get_or_create(key=TRANSFER_WINDOW_KEY, defaults={"value": "CLOSED"})
        self.stdout.write(self.style.SUCCESS("Defaults ready."))
