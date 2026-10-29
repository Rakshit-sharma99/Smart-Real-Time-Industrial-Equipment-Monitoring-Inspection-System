from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.permissions import admin_required
from equipment import services as equipment_services
from equipment.models import Equipment

from .forms import SimulatorSettingsForm
from .models import SimulatorSettings
from .simulation import primary_metric


@admin_required
def simulator_settings(request):
    sim = SimulatorSettings.load()
    form = SimulatorSettingsForm(request.POST or None, instance=sim)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Simulator settings saved. The simulator picks them up on its next tick.")
        return redirect("monitoring:simulator_settings")

    seconds_since = None
    if sim.last_ingest_at:
        seconds_since = int((timezone.now() - sim.last_ingest_at).total_seconds())
    running = seconds_since is not None and seconds_since <= sim.interval_seconds * 2 + 5

    machines = [
        {"equipment": eq, "primary_metric": primary_metric(eq)}
        for eq in Equipment.objects.active()
    ]
    context = {
        "form": form,
        "sim": sim,
        "seconds_since": seconds_since,
        "running": running,
        "machines": machines,
        "sim_modes": Equipment.SimMode.choices,
        "page_title": "Simulator",
        "settings_tab": "simulator",
    }
    return render(request, "monitoring/simulator_settings.html", context)


@admin_required
@require_POST
def set_sim_mode(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    mode = request.POST.get("sim_mode", "")
    if mode not in Equipment.SimMode.values:
        messages.error(request, "Unknown simulator mode.")
    else:
        equipment_services.set_sim_mode(equipment, mode)
        messages.success(
            request, f"{equipment.equipment_code} simulator mode set to {equipment.get_sim_mode_display()}."
        )
    return redirect("monitoring:simulator_settings")
