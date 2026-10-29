from django import forms
from django.contrib.auth.models import User

from core.forms import BootstrapFormMixin
from equipment.models import Equipment
from inspections.forms import assignable_users

from .models import MaintenanceTask


def user_label(user):
    return f"{user.get_full_name() or user.username} ({user.username})"


class TaskForm(BootstrapFormMixin, forms.Form):
    equipment = forms.ModelChoiceField(queryset=Equipment.objects.active())
    title = forms.CharField(max_length=160)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    priority = forms.ChoiceField(choices=MaintenanceTask.Priority.choices, initial=MaintenanceTask.Priority.MEDIUM)
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    assignee = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False, label="Assign to",
        help_text="Optional. Leave empty to keep the task Open and assign it later.",
    )

    def __init__(self, *args, lock_equipment=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = assignable_users()
        self.fields["assignee"].label_from_instance = user_label
        if lock_equipment:
            # Converting a defect/alert: the machine comes from the source and cannot change.
            self.fields["equipment"].disabled = True
            self.fields["equipment"].queryset = Equipment.objects.all()


class AssignTaskForm(BootstrapFormMixin, forms.Form):
    assignee = forms.ModelChoiceField(queryset=User.objects.none(), label="Assign to")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = assignable_users()
        self.fields["assignee"].label_from_instance = user_label


class CompleteTaskForm(BootstrapFormMixin, forms.Form):
    resolution_notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What was done? Parts replaced, readings after the fix..."}),
        help_text="Required to complete the task.",
    )


class TaskFilterForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(
        choices=[("open", "Open (not completed)"), ("all", "All statuses"), *MaintenanceTask.Status.choices],
        required=False,
    )
    priority = forms.ChoiceField(choices=[("", "Any priority"), *MaintenanceTask.Priority.choices], required=False)

    def filter(self, queryset):
        if not self.is_valid():
            return queryset.open()
        status = self.cleaned_data["status"] or "open"
        if status == "open":
            queryset = queryset.open()
        elif status != "all":
            queryset = queryset.filter(status=status)
        if self.cleaned_data["priority"]:
            queryset = queryset.filter(priority=self.cleaned_data["priority"])
        return queryset
