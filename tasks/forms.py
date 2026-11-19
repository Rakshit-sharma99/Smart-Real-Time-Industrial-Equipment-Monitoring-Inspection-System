from django import forms
from django.contrib.auth.models import User

from core.forms import BootstrapFormMixin
from inspections.forms import TableCheckForm
from tables.forms import person, waiters_of
from tables.models import RestaurantTable

from .models import WaiterTask


class TaskForm(BootstrapFormMixin, forms.Form):
    table = forms.ModelChoiceField(queryset=RestaurantTable.objects.none())
    task_type = forms.ChoiceField(label="Task", choices=WaiterTask.TaskType.choices, initial=WaiterTask.TaskType.FULL_RESET)
    priority = forms.ChoiceField(choices=WaiterTask.Priority.choices, initial=WaiterTask.Priority.NORMAL)
    due_minutes = forms.IntegerField(label="Deadline (minutes from now)", min_value=1, max_value=600)
    assignee = forms.ModelChoiceField(queryset=User.objects.none(), required=False, label="Assign to",
                                      empty_label="Not yet (keep it Open)")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["table"].queryset = RestaurantTable.objects.filter(restaurant=restaurant, is_active=True)
        self.fields["assignee"].queryset = waiters_of(restaurant)
        self.fields["assignee"].label_from_instance = person
        self.fields["due_minutes"].initial = restaurant.cleaning_deadline_minutes


class WaiterChoiceForm(BootstrapFormMixin, forms.Form):
    waiter = forms.ModelChoiceField(queryset=User.objects.none(), label="Waiter")

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["waiter"].queryset = waiters_of(restaurant)
        self.fields["waiter"].label_from_instance = person


class PriorityForm(BootstrapFormMixin, forms.Form):
    priority = forms.ChoiceField(choices=WaiterTask.Priority.choices)


class ProgressForm(BootstrapFormMixin, forms.Form):
    note = forms.CharField(max_length=300, widget=forms.TextInput(attrs={"placeholder": "e.g. Dishes cleared, wiping now"}))


class CompleteTaskForm(TableCheckForm):
    """Completing a task = answering the table check (pre-filled with "all done")."""


class TaskFilterForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(required=False, choices=[
        ("open", "Not completed"), ("all", "All statuses"), *WaiterTask.Status.choices,
    ])
    waiter = forms.ModelChoiceField(queryset=User.objects.none(), required=False, empty_label="Any waiter")
    priority = forms.ChoiceField(required=False, choices=[("", "Any priority"), *WaiterTask.Priority.choices])

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["waiter"].queryset = waiters_of(restaurant)
        self.fields["waiter"].label_from_instance = person

    def filter(self, tasks):
        if not self.is_valid():
            return tasks.open()
        data = self.cleaned_data
        status = data["status"] or "open"
        if status == "open":
            tasks = tasks.open()
        elif status != "all":
            tasks = tasks.filter(status=status)
        if data["waiter"]:
            tasks = tasks.filter(assigned_to=data["waiter"])
        if data["priority"]:
            tasks = tasks.filter(priority=data["priority"])
        return tasks
