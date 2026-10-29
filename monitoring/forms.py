from django import forms

from core.forms import BootstrapFormMixin

from .models import SimulatorSettings


class SimulatorSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SimulatorSettings
        fields = ["interval_seconds", "noise_pct"]
