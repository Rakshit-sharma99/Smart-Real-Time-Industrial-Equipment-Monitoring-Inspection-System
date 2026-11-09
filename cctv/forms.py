from django import forms

from core.forms import BootstrapFormMixin

from .models import Camera


class CameraForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Camera
        fields = ["name", "source_type", "source", "is_active", "detection_interval_seconds", "min_confidence",
                  "frames_to_confirm"]
        widgets = {"source": forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"})}

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.restaurant = restaurant
        if self.instance.pk and "://" in (self.instance.source or ""):
            self.fields["source"].help_text = (
                f"Current: {self.instance.masked_source} (credentials hidden). Enter the full URL again to change it."
            )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Camera.objects.filter(restaurant=self.restaurant, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A camera with this name already exists.")
        return name
