from django.urls import path
from . import views

urlpatterns = [
    # ── Public pages ─────────────────────────────────────
    path("", views.home, name="home"),
    path("index", views.home, name="home"),
    path("claim/<int:pk>/result/", views.claim_result, name="claim_result"),
    path("refund/<int:claim_id>/", views.refund_view, name="refund"),
    path("claims/", views.claim_views, name="claims"),
    path("rights/", views.air_passenger_rights_view, name="air_passenger_rights"),
    path("about/", views.how_it_works_view, name="how_it_works"),
    path("api/airport-lookup/", views.airport_lookup, name="airport_lookup"),

    # ── Auth ─────────────────────────────────────────────
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),

    # ── Support / Call pages ─────────────────────────────
    path("support/", views.support_view, name="support"),
    path("staff/console/", views.staff_console, name="staff_console"),

    # ── Call API (mirrors the calls app) ─────────────────
    path("api/calls/start/", views.start_call, name="start_call"),
    path("api/calls/incoming/", views.incoming_calls, name="incoming_calls"),
    path("api/calls/<int:call_id>/", views.call_detail, name="call_detail"),
    path("api/calls/<int:call_id>/answer/", views.answer_call, name="answer_call"),
    path("api/calls/<int:call_id>/end/", views.end_call, name="end_call"),

    # ── Staff Claim Hub ──────────────────────────────────
    path("staff/hub/", views.staff_hub, name="staff_hub"),
    path("staff/hub/claim/<int:claim_id>/json/", views.claim_detail_json, name="claim_detail_json"),
    path("staff/claim/<int:claim_id>/approve/", views.claim_approve, name="claim_approve"),

    # ── Chat (customer) ──────────────────────────────────
    path("chat/", views.chat_customer_view, name="chat_customer"),
    path("chat/messages/", views.chat_customer_messages, name="chat_customer_messages"),
    path("chat/send/", views.chat_customer_send, name="chat_customer_send"),

    # ── Chat (staff) ─────────────────────────────────────
    path("staff/chat/", views.chat_inbox_view, name="chat_inbox"),
    path("staff/chat/<int:pk>/", views.chat_staff_thread, name="chat_staff_thread"),
    path("staff/chat/<int:pk>/messages/", views.chat_staff_messages, name="chat_staff_messages"),
    path("staff/chat/<int:pk>/send/", views.chat_staff_send, name="chat_staff_send"),
]