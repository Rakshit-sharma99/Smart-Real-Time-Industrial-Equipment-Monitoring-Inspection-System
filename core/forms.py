from django import forms

from tables.models import Restaurant, RestaurantSection


class BootstrapFormMixin:
    """Adds Bootstrap 5 CSS classes to every widget so templates stay simple."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                css_class = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            else:
                css_class = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()

    def full_clean(self):
        super().full_clean()
        # Mark fields with errors so Bootstrap shows them in red.
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()


class RestaurantForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Restaurant
        fields = ["name", "address", "opens_at", "closes_at", "reservation_minutes",
                  "reservation_hold_minutes", "cleaning_deadline_minutes"]
        widgets = {
            "opens_at": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "closes_at": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }


class SectionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = RestaurantSection
        fields = ["name", "floor", "sort_order"]

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.restaurant = restaurant

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = RestaurantSection.objects.filter(restaurant=self.restaurant, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A section with this name already exists.")
        return name
