from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm, UserCreationForm
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
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].initial = self.instance.profile.phone

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.profile.phone = self.cleaned_data["phone"]
            user.profile.save(update_fields=["phone"])
        return user


class StaffCreateForm(BootstrapFormMixin, UserCreationForm):
    role = forms.ChoiceField(choices=Role.choices, initial=Role.WAITER)
    phone = forms.CharField(max_length=20, required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.restaurant = restaurant

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            profile = user.profile
            profile.role, profile.phone, profile.restaurant = self.cleaned_data["role"], self.cleaned_data["phone"], self.restaurant
            profile.save()
        return user


class StaffUpdateForm(BootstrapFormMixin, forms.ModelForm):
    """A manager edits a staff member. Managers cannot lock themselves out."""

    role = forms.ChoiceField(choices=Role.choices)
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active"]
        labels = {"is_active": "Account active (can log in)"}

    def __init__(self, *args, acting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.acting_user = acting_user
        self.fields["role"].initial = self.instance.profile.role
        self.fields["phone"].initial = self.instance.profile.phone

    def clean(self):
        cleaned = super().clean()
        if self.acting_user is not None and self.instance.pk == self.acting_user.pk:
            if not cleaned.get("is_active", True):
                self.add_error("is_active", "You cannot deactivate your own account.")
            if cleaned.get("role") != Role.MANAGER:
                self.add_error("role", "You cannot remove your own Manager role.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.profile.role, user.profile.phone = self.cleaned_data["role"], self.cleaned_data["phone"]
            user.profile.save(update_fields=["role", "phone"])
        return user
