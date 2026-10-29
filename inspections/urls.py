from django.urls import path

from . import views

app_name = "inspections"

urlpatterns = [
    path("", views.InspectionListView.as_view(), name="list"),
    path("assign/", views.inspection_assign, name="assign"),
    path("defects/", views.DefectListView.as_view(), name="defects"),
    path("adhoc/<slug:code>/", views.inspection_start_adhoc, name="start_adhoc"),
    path("photos/<int:pk>/", views.response_photo, name="photo"),
    path("templates/", views.template_list, name="template_list"),
    path("templates/new/", views.template_edit, name="template_create"),
    path("templates/<int:pk>/", views.template_edit, name="template_edit"),
    path("<int:pk>/", views.inspection_detail, name="detail"),
    path("<int:pk>/start/", views.inspection_start, name="start"),
    path("<int:pk>/perform/", views.inspection_perform, name="perform"),
]
