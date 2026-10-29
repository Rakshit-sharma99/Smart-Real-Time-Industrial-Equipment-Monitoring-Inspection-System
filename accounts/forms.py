from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.contrib.auth.models import User

from core.forms import BootstrapFormMixin

from .models import Role


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    pass


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]


class UserCreateForm(BootstrapFormMixin, UserCreationForm):
    role = forms.ChoiceField(choices=Role.choices, initial=Role.INSPECTOR)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.profile.role = self.cleaned_data["role"]
            user.profile.save()
        return user


class UserUpdateForm(BootstrapFormMixin, forms.ModelForm):
    """Admin edits another user. Admins cannot lock themselves out."""

    role = forms.ChoiceField(choices=Role.choices)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active"]
        labels = {"is_active": "Account active (can log in)"}

    def __init__(self, *args, acting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.acting_user = acting_user
        self.fields["role"].initial = self.instance.profile.role

    def clean(self):
        cleaned = super().clean()
        if self.acting_user is not None and self.instance.pk == self.acting_user.pk:
            if not cleaned.get("is_active", True):
                self.add_error("is_active", "You cannot deactivate your own account.")
            if cleaned.get("role") != Role.ADMIN and not self.instance.is_superuser:
                self.add_error("role", "You cannot remove your own Admin role.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.profile.role = self.cleaned_data["role"]
            user.profile.save()
        return user
