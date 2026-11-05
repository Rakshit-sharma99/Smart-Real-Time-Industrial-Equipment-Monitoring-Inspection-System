from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from core.permissions import floor_staff_required, manager_required, role_required
from core.services import run_periodic_checks
from inspections.forms import AFTER_CLEANING
from tables import services as table_services
from tables.forms import waiters_of
from tables.models import RestaurantTable

from . import services
from .forms import CompleteTaskForm, PriorityForm, ProgressForm, TaskFilterForm, TaskForm, WaiterChoiceForm
from .models import WaiterTask

waiter_required = role_required(Role.WAITER)

# Most urgent first: URGENT, HIGH, NORMAL, LOW.
PRIORITY_RANK = Case(
    When(priority=WaiterTask.Priority.URGENT, then=Value(0)),
    When(priority=WaiterTask.Priority.HIGH, then=Value(1)),
    When(priority=WaiterTask.Priority.NORMAL, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)


# --- Waiter dashboard (mobile first) ------------------------------------------------------------


def waiter_context(request):
    mine = WaiterTask.objects.for_restaurant(request.restaurant).filter(assigned_to=request.user)
    active = mine.active().select_related("table").annotate(rank=PRIORITY_RANK).order_by("rank", "due_at")
    my_tables = table_services.with_status(
        table_services.live_tables(request.restaurant).filter(assigned_waiter=request.user, is_active=True)
    )
    return {
        "my_tasks": active,
        "my_tables": my_tables,
        "done_today": mine.filter(status=WaiterTask.Status.COMPLETED, completed_at__date=timezone.localdate()).count(),
    }


@waiter_required
def waiter_home(request):
    run_periodic_checks(request.restaurant)
    return render(request, "tasks/waiter_home.html", {"page_title": "My tasks", **waiter_context(request)})


@waiter_required
def waiter_fragment(request):
    run_periodic_checks(request.restaurant)
    return render(request, "tasks/_waiter_board.html", waiter_context(request))


# --- Manager list ---------------------------------------------------------------------------------


def task_list_context(request):
    """Filtered, paginated tasks plus progress per waiter. Shared by the page and its live fragment."""
    filter_form = TaskFilterForm(request.GET or None, restaurant=request.restaurant)
    tasks = WaiterTask.objects.for_restaurant(request.restaurant).select_related("table", "assigned_to")
    tasks = filter_form.filter(tasks).annotate(rank=PRIORITY_RANK).order_by("status", "rank", "due_at")
    page_obj = Paginator(tasks, 25).get_page(request.GET.get("page"))
    today = timezone.localdate()
    active = Q(waiter_tasks__status__in=[WaiterTask.Status.ASSIGNED, WaiterTask.Status.IN_PROGRESS])
    done = Q(waiter_tasks__status=WaiterTask.Status.COMPLETED, waiter_tasks__completed_at__date=today)
    return {
        "filter_form": filter_form,
        "tasks": page_obj.object_list,
        "page_obj": page_obj,
        "waiter_progress": waiters_of(request.restaurant).annotate(
            active=Count("waiter_tasks", filter=active), done_today=Count("waiter_tasks", filter=done),
        ),
        "query": request.GET.urlencode(),
    }


@manager_required
def task_list(request):
    return render(request, "tasks/list.html", {"page_title": "Waiter tasks", **task_list_context(request)})


@manager_required
def task_list_fragment(request):
    return render(request, "tasks/_task_table.html", task_list_context(request))


@manager_required
def task_create(request):
    initial = {}
    table = None
    if request.GET.get("table"):
        table = get_object_or_404(RestaurantTable, pk=request.GET["table"], restaurant=request.restaurant)
        initial = {"table": table, "assignee": table.assigned_waiter}
    form = TaskForm(request.POST or None, initial=initial, restaurant=request.restaurant)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            task = services.create_task(
                table=data["table"], task_type=data["task_type"], priority=data["priority"],
                description=data["description"], created_by=request.user, assignee=data["assignee"],
                due_at=timezone.now() + timedelta(minutes=data["due_minutes"]),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"{task.task_code} created" + (f" and assigned to {task.assigned_to.get_full_name()}." if task.assigned_to else "."))
            return redirect(request.POST.get("next") or task_url(task))
    return render(request, "tasks/form.html", {"form": form, "table": table, "page_title": "New waiter task"})


# --- One task ---------------------------------------------------------------------------------------


def task_url(task):
    return reverse("tasks:detail", args=[task.pk])


def visible_task(request, pk):
    queryset = WaiterTask.objects.visible_to(request.user, request.restaurant)
    return get_object_or_404(queryset.select_related("table", "assigned_to", "created_by"), pk=pk)


@floor_staff_required
def task_detail(request, pk, complete_form=None, status=200):
    task = visible_task(request, pk)
    is_manager = request.role == Role.MANAGER
    context = {
        "task": task,
        "history": task.history.select_related("changed_by"),
        "allowed": services.allowed_transitions(task, request.user),
        "is_assignee": task.assigned_to_id == request.user.pk,
        "complete_form": complete_form or CompleteTaskForm(initial=AFTER_CLEANING),
        "progress_form": ProgressForm(),
        "page_title": f"{task.task_code} · {task.table.label}",
    }
    if is_manager:
        context["waiter_form"] = WaiterChoiceForm(restaurant=request.restaurant)
        context["priority_form"] = PriorityForm(initial={"priority": task.priority})
    return render(request, "tasks/detail.html", context, status=status)


def run(request, task, action, success):
    """Call a task service; show validation problems as a message. Permission errors become 403."""
    try:
        task = action()
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, success.format(task=task))
    next_url = request.POST.get("next")
    return redirect(next_url if next_url and next_url.startswith("/") else task_url(task))


@manager_required
@require_POST
def assign(request, pk):
    task = visible_task(request, pk)
    form = WaiterChoiceForm(request.POST, restaurant=request.restaurant)
    if not form.is_valid():
        messages.error(request, "Choose a waiter.")
        return redirect(task_url(task))
    waiter = form.cleaned_data["waiter"]
    if task.status == WaiterTask.Status.OPEN:
        return run(request, task, lambda: services.assign(task, waiter, user=request.user), "{task.task_code} assigned.")
    return run(request, task, lambda: services.reassign(task, waiter, user=request.user), "{task.task_code} reassigned.")


@manager_required
@require_POST
def unassign(request, pk):
    task = visible_task(request, pk)
    return run(request, task, lambda: services.unassign(task, user=request.user), "{task.task_code} is open again.")


@manager_required
@require_POST
def priority(request, pk):
    task = visible_task(request, pk)
    form = PriorityForm(request.POST)
    if not form.is_valid():
        return redirect(task_url(task))
    return run(request, task, lambda: services.change_priority(task, form.cleaned_data["priority"], user=request.user),
               "Priority updated.")


@floor_staff_required
@require_POST
def accept(request, pk):
    task = visible_task(request, pk)
    return run(request, task, lambda: services.accept(task, user=request.user), "{task.task_code} accepted.")


@floor_staff_required
@require_POST
def start(request, pk):
    task = visible_task(request, pk)
    return run(request, task, lambda: services.start(task, user=request.user),
               "{task.task_code} started - the table shows Cleaning in progress.")


@floor_staff_required
@require_POST
def progress(request, pk):
    task = visible_task(request, pk)
    return run(request, task, lambda: services.add_progress(task, request.POST.get("note", ""), user=request.user),
               "Progress note added.")


@floor_staff_required
@require_POST
def complete(request, pk):
    task = visible_task(request, pk)
    form = CompleteTaskForm(request.POST)
    if not form.is_valid():
        return task_detail(request, pk, complete_form=form, status=400)
    try:
        services.complete(task, user=request.user, check=form.answers(), remarks=form.cleaned_data["remarks"])
    except ValidationError as exc:
        form.add_error(None, exc)
        return task_detail(request, pk, complete_form=form, status=400)
    task.refresh_from_db()
    table = table_services.with_status([table_services.live_tables(request.restaurant).get(pk=task.table_id)])[0]
    messages.success(request, f"{task.task_code} completed. {table.label} is now {table.status_label}.")
    if request.role == Role.WAITER:
        return redirect("tasks:waiter_home")
    return redirect(task_url(task))
