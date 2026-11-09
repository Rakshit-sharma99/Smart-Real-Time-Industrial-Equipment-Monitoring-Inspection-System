import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.permissions import manager_required
from tables import services as table_services
from tables.models import RestaurantTable

from . import services
from .forms import CameraForm
from .models import Camera, TableZone


def restaurant_cameras(request):
    return Camera.objects.filter(restaurant=request.restaurant)


def live_tables_by_id(request):
    return {t.pk: t for t in table_services.with_status(table_services.live_tables(request.restaurant))}


# --- Live console ------------------------------------------------------------------------


@manager_required
def console(request, pk=None):
    cameras = list(restaurant_cameras(request).filter(is_active=True))
    camera = None
    if cameras:
        camera = next((c for c in cameras if str(c.pk) == str(pk)), cameras[0]) if pk else cameras[0]
    context = {"cameras": cameras, "camera": camera, "page_title": "Live CCTV"}
    if camera:
        tables = live_tables_by_id(request)
        context["state"] = services.console_state(camera, tables)
        context["zone_tables"] = [tables.get(z.table_id) for z in camera.zones.all() if z.table_id in tables]
        if request.GET.get("fragment"):
            return render(request, "cctv/_zone_tables.html", context)
    return render(request, "cctv/console.html", context)


@manager_required
def console_state(request, pk):
    camera = get_object_or_404(restaurant_cameras(request), pk=pk)
    return JsonResponse(services.console_state(camera, live_tables_by_id(request)))


@manager_required
def frame(request, pk):
    camera = get_object_or_404(restaurant_cameras(request), pk=pk)
    jpeg = services.latest_frame(camera)
    if jpeg is None:
        raise Http404("No recent frame.")
    response = HttpResponse(jpeg, content_type="image/jpeg")
    response["Cache-Control"] = "no-store"
    return response


# --- Zone editor ------------------------------------------------------------------------------


@manager_required
def zones(request, pk):
    camera = get_object_or_404(restaurant_cameras(request), pk=pk)
    tables = RestaurantTable.objects.filter(restaurant=request.restaurant, is_active=True).order_by("number")
    return render(request, "cctv/zones.html", {
        "camera": camera, "tables": tables, "has_frame": services.latest_frame(camera) is not None,
        "zones_json": json.dumps([{"id": z.pk, "table": z.table_id, "label": z.table.label, "points": z.points}
                                  for z in camera.zones.select_related("table")]),
        "page_title": f"Table zones · {camera.name}",
    })


@manager_required
@require_POST
def save_zones(request, pk):
    """Replace all zones of a camera: JSON body [{"table": id, "points": [[x, y], ...]}, ...]."""
    camera = get_object_or_404(restaurant_cameras(request), pk=pk)
    try:
        payload = json.loads(request.body or "[]")
        assert isinstance(payload, list)
    except (ValueError, AssertionError):
        return JsonResponse({"error": "Send a JSON list of zones.", "details": {}}, status=400)
    table_ids = {int(item.get("table", 0)) for item in payload}
    tables = {t.pk: t for t in RestaurantTable.objects.filter(restaurant=request.restaurant, pk__in=table_ids)}
    if len(tables) != len(table_ids):
        return JsonResponse({"error": "Unknown table in the zones.", "details": {}}, status=400)
    new_zones = []
    for item in payload:
        zone = TableZone(camera=camera, table=tables[int(item["table"])], points=item.get("points"))
        try:
            zone.clean()
        except ValidationError as exc:
            return JsonResponse({"error": "Invalid zone.", "details": exc.message_dict}, status=400)
        new_zones.append(zone)
    camera.zones.all().delete()
    TableZone.objects.bulk_create(new_zones)
    return JsonResponse({"saved": len(new_zones)})


# --- Camera settings ----------------------------------------------------------------------------


@manager_required
def camera_list(request):
    cameras = restaurant_cameras(request).prefetch_related("zones")
    return render(request, "cctv/camera_list.html", {"cameras": cameras, "page_title": "Cameras", "settings_tab": "cameras"})


@manager_required
def camera_edit(request, pk=None):
    camera = get_object_or_404(restaurant_cameras(request), pk=pk) if pk else None
    form = CameraForm(request.POST or None, instance=camera, restaurant=request.restaurant)
    if request.method == "POST" and form.is_valid():
        camera = form.save(commit=False)
        camera.restaurant = request.restaurant
        camera.save()
        messages.success(request, f"Camera '{camera.name}' saved. Restart 'python manage.py run_cameras' to pick up source changes.")
        return redirect("cctv:camera_list")
    return render(request, "cctv/camera_form.html", {
        "form": form, "camera": camera, "settings_tab": "cameras",
        "page_title": f"Edit {camera.name}" if camera else "New camera",
    })


@manager_required
@require_POST
def camera_delete(request, pk):
    camera = get_object_or_404(restaurant_cameras(request), pk=pk)
    name = camera.name
    camera.delete()
    messages.success(request, f"Camera '{name}' removed.")
    return redirect("cctv:camera_list")
