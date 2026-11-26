"""Supabase exposes every table in the `public` schema through its public REST API. Django's tables (including user
password hashes) must NOT be reachable that way. Enabling Row Level Security with no policies blocks the API while
Django (which connects as the table owner and bypasses RLS) keeps working. Safe to run repeatedly; no-op on SQLite."""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Enable Row Level Security on all public tables (PostgreSQL / Supabase only)."

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            self.stdout.write("Not PostgreSQL - skipped.")
            return
        try:
            with connection.cursor() as cur:
                cur.execute("select tablename from pg_tables where schemaname = 'public'")
                tables = [r[0] for r in cur.fetchall()]
                for t in tables:
                    cur.execute(f'alter table public."{t}" enable row level security')
            self.stdout.write(self.style.SUCCESS(f"Row Level Security enabled on {len(tables)} tables."))
        except Exception as exc:  # never break the deploy over this
            self.stderr.write(f"Could not enable RLS automatically ({exc}). Run the SQL from the README in Supabase instead.")
