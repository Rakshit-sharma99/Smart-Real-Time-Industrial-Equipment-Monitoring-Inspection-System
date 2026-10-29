from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.permissions import admin_required
from equipment import services as equipment_services
from equipment.models import Equipment

from equipment.metrics import METRICS, classify, tone_for

from .forms import SimulatorSettingsForm
from .models import SimulatorSettings
from .services import latest_readings
from .simulation import primary_metric


def live_monitoring(request):
    """One card per active machine plus a live chart for the selected machine and metric."""
    machines = list(Equipment.objects.active())
    latest = latest_readings(machines)
    cards = []
    for equipment in machines:
        reading = latest.get(equipment.pk)
        metrics = []
        for metric, warn, crit in equipment.threshold_rows():
            value = reading.value_for(metric) if reading else None
            status = classify(value, warn, crit) if value is not None else None
            metrics.append({"metric": metric, "value": value, "warn": warn, "crit": crit, "tone": tone_for(status)})
        cards.append({"equipment": equipment, "reading": reading, "metrics": metrics})

    codes = [m.equipment_code for m in machines]
    selected = request.GET.get("eq") if request.GET.get("eq") in codes else (codes[0] if codes else None)
    metric_keys = [m.key for m in METRICS]
    selected_metric = request.GET.get("metric") if request.GET.get("metric") in metric_keys else "temperature"
    context = {
        "page_title": "Live Monitoring",
        "cards": cards,
        "selected_code": selected,
        "selected_metric": selected_metric,
        "metrics": METRICS,
    }
    return render(request, "monitoring/live.html", context)


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
