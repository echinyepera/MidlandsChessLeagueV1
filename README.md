# Midlands Chess League - Django platform

The approved HTML prototype, converted into a real database-driven Django app, following the Master Specification v2.0.

## Run it locally (5 minutes)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: py -m venv .venv  then  .venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations league                  # creates league/migrations/0001_initial.py  (COMMIT IT)
python manage.py migrate
python manage.py createsuperuser                        # YOU create the Super Admin account
python manage.py seed_defaults                          # leadership entries + site info
python manage.py test                                   # checks the points / table / lineup rules
python manage.py runserver
```
- Public site: http://127.0.0.1:8000/
- Sign in at http://127.0.0.1:8000/login/ with the superuser you created -> the Admin console.
- **Captains are created by the Admin only**: Console -> Clubs -> (club) -> Club captain. Enter name + email; a temporary
  password is shown once, and the captain must change it at first login. No account is ever created automatically.

## Deploy: Render (web) + Supabase (database)
1. **Supabase**: New project -> set a database password -> click **Connect** -> copy the **Session pooler** URI
   (`postgresql://postgres.<ref>:<password>@aws-...pooler.supabase.com:5432/postgres`). Do not use the "Direct connection"
   string: Render's free tier is IPv4-only and Supabase's direct host is IPv6-only. URL-encode special characters in the password.
2. Locally: `python manage.py makemigrations league`, commit the `league/migrations/` folder, push to GitHub.
3. **Render**: New -> Blueprint -> pick the repo. Enter `DATABASE_URL` (the Session pooler URI) and
   `DJANGO_SUPERUSER_USERNAME / EMAIL / PASSWORD` (your own choice). The build migrates the database, locks it down
   with Row Level Security (`enable_rls`), and creates your Super Admin on the first deploy.
4. Open the site, sign in at `/login/`, fill in Site Info, clubs, players, captains.
If `enable_rls` printed a warning, run this once in Supabase's SQL editor:
```sql
do $$ declare r record; begin
  for r in select tablename from pg_tables where schemaname='public' loop
    execute format('alter table public.%I enable row level security', r.tablename);
  end loop; end $$;
```
Logos: use the **Logo URL** field on each club (uploaded files are not stored on Render's free plan).

## Where things live
| Spec item | File |
|---|---|
| Models (incl. Lineup, Transfer, Official, SiteInfo, Phase-2 hooks) | `league/models.py` |
| Points, league table + tie-breaks, round-robin, catch-up, byes, smart alerts, WhatsApp text | `league/services.py` |
| Auto-recalculation on result save/delete | `league/signals.py` |
| Public / Captain / Admin-console views | `league/views_public.py`, `views_captain.py`, `views_console.py` |
| Templates built from the prototype | `templates/` + `league/static/league/css/style.css` (prototype CSS, unchanged) |

## Decisions worth knowing
- **7 boards** (prototype used 5). Every club needs at least 7 players to field a team.
- **Points**: board 1/0.5/0; team 3/1/0; table = match (team) points, then game (board) points - confirmed by the President.
- **Captains** log in with email + temporary password (prototype PINs replaced). Admin creates them on the club page; password must be changed at first login.
- Home club plays White on all boards (as in the prototype).
- Lineup deadline default is 24h before the match (`lineup_deadline_hours`, open item #3).
- Transfer sets a player's status to "Transferred" and moves them; their old club's submitted lineups containing them are cleared.
