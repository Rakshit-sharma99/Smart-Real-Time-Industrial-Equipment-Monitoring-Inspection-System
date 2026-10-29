"""
All JSON API endpoints in one place, mounted at /api/v1/.

Every view is marked login_not_required so that DRF (not the login-redirect
middleware) answers unauthenticated calls with a JSON error. Each view still
declares its own authentication and permission classes.
"""
from django.contrib.auth.decorators import login_not_required
from django.urls import path

from monitoring import api as monitoring_api

app_name = "api"

urlpatterns = [
    path("ingest/", login_not_required(monitoring_api.IngestView.as_view()), name="ingest"),
]
