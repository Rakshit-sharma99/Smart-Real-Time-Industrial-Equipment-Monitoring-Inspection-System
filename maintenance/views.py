from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from core.permissions import RoleRequiredMixin, admin_required
from equipment.models import Equipment
from inspections.models import Defect
from monitoring.models import Alert

from . import services
from .forms import AssignTaskForm, CompleteTaskForm, TaskFilterForm, TaskForm
from .models import MaintenanceTask

Status = MaintenanceTask.Status


class TaskListView(RoleRequiredMixin, ListView):
    template_name = "maintenance/list.html"
    context_object_name = "tasks"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = TaskFilterForm(self.request.GET or {"status": "open"})
        visible = MaintenanceTask.objects.visible_to(self.request.user).select_related("equipment", "assigned_to")
        return self.filter_form.filter(visible)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(filter_form=self.filter_form, page_title="Maintenance")
        return context


def visible_task(request, pk):
    queryset = MaintenanceTask.objects.visible_to(request.user).select_related(
        "equipment", "assigned_to", "created_by", "alert", "defect"
    )
    return get_object_or_404(queryset, pk=pk)


def task_detail(request, pk, assign_form=None, complete_form=None, status=200):
    task = visible_task(request, pk)
    allowed = services.allowed_transitions(task, request.user)
    context = {
        "task": task,
        "history": task.history.select_related("changed_by"),
        "allowed": allowed,
        "assign_form": assign_form or AssignTaskForm(),
        "complete_form": complete_form or CompleteTaskForm(),
        "page_title": f"{task.task_code} · {task.equipment.equipment_code}",
    }
    return render(request, "maintenance/detail.html", context, status=status)


@require_POST
def task_change_status(request, pk):
    """One endpoint for every transition; the services table decides what is allowed."""
    task = visible_task(request, pk)
    to_status = request.POST.get("to_status", "")
    if to_status not in Status.values:
        messages.error(request, "Unknown status.")
        return redirect("maintenance:detail", pk=pk)

    assign_form = complete_form = None
    kwargs = {"note": request.POST.get("note", "")}
    if to_status == Status.ASSIGNED:
        assign_form = AssignTaskForm(request.POST)
        if not assign_form.is_valid():
            return task_detail(request, pk, assign_form=assign_form, status=400)
        kwargs["assignee"] = assign_form.cleaned_data["assignee"]
    if to_status == Status.COMPLETED:
        complete_form = CompleteTaskForm(request.POST)
        if not complete_form.is_valid():
            return task_detail(request, pk, complete_form=complete_form, status=400)
        kwargs["resolution_notes"] = complete_form.cleaned_data["resolution_notes"]

    try:
        task = services.change_status(task, to_status, request.user, **kwargs)
    except (services.TransitionError, ValidationError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, f"{task.task_code} is now {task.get_status_display()}.")
    return redirect("maintenance:detail", pk=pk)


@admin_required
def task_create(request):
    """New task. ?defect=<id> or ?alert=<id> converts that item (pre-filled, linked)."""
    defect = alert = None
    initial = {}
    if request.GET.get("defect"):
        defect = get_object_or_404(Defect.objects.select_related("equipment"), pk=request.GET["defect"])
        initial = services.prefill_from_defect(defect)
    elif request.GET.get("alert"):
        alert = get_object_or_404(Alert.objects.select_related("equipment"), pk=request.GET["alert"])
        initial = services.prefill_from_alert(alert)
    elif request.GET.get("equipment"):
        initial["equipment"] = Equipment.objects.filter(equipment_code=request.GET["equipment"]).first()

    form = TaskForm(request.POST or None, initial=initial, lock_equipment=bool(defect or alert))
    if request.method == "POST" and form.is_valid():
        try:
            task = services.create_task(created_by=request.user, alert=alert, defect=defect, **form.cleaned_data)
        except services.TransitionError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{task.task_code} created.")
            return redirect("maintenance:detail", pk=task.pk)
    context = {"form": form, "defect": defect, "alert": alert, "page_title": "New maintenance task"}
    return render(request, "maintenance/form.html", context)
