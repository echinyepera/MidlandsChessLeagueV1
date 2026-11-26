from django.urls import path

from . import views_captain as cap
from . import views_console as con
from . import views_public as pub

app_name = "league"

urlpatterns = [
    # public
    path("", pub.home, name="home"),
    path("login/", pub.StaffLoginView.as_view(), name="login"),
    path("logout/", pub.logout_view, name="logout"),
    path("go/", pub.after_login, name="after_login"),
    # captain portal
    path("captain/", cap.captain_dashboard, name="captain_dashboard"),
    path("captain/lineup/<int:fixture_id>/", cap.submit_lineup, name="submit_lineup"),
    path("captain/password/", cap.CaptainPasswordChange.as_view(), name="captain_password"),
    # admin console
    path("console/", con.dashboard, name="console_dashboard"),
    path("console/clubs/", con.clubs, name="console_clubs"),
    path("console/clubs/<int:pk>/", con.club_detail, name="console_club"),
    path("console/transfers/", con.transfers, name="console_transfers"),
    path("console/fixtures/", con.fixtures, name="console_fixtures"),
    path("console/results/", con.results, name="console_results"),
    path("console/notices/", con.notices, name="console_notices"),
    path("console/officials/", con.officials, name="console_officials"),
    path("console/site/", con.site_info, name="console_site"),
    # WhatsApp exports
    path("console/export/fixtures/<int:round_number>/", con.export_fixtures, name="export_fixtures"),
    path("console/export/results/<int:round_number>/", con.export_results, name="export_results"),
    path("console/export/board/<int:fixture_id>/", con.export_board_results, name="export_board_results"),
    path("console/export/transfer-window/", con.export_transfer_window, name="export_transfer_window"),
]
