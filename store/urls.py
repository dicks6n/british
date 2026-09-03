from django.urls import path
from . import views
from store.views import home, refund_view ,claim_views ,air_passenger_rights_view

urlpatterns = [
    path("", views.home, name="home"),
    path("index", views.home, name="home"),

    path('refund/<int:claim_id>/', refund_view, name='refund'),
    path('claims/', claim_views, name='claims'),
    path('rights/', air_passenger_rights_view, name='air_passenger_rights'),
    path('about/', views.how_it_works_view, name='how_it_works'),
    # 🔐 AUTH
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),
]
