from django import forms


class AppForm(forms.Form):
    name = forms.CharField(label="App name", max_length=120)
    store_url = forms.CharField(label="Google Play link", required=False,
                                help_text="The agent reads the listing and looks at your screenshots.",
                                widget=forms.URLInput(attrs={"placeholder": "https://play.google.com/store/apps/details?id=com.your.app"}))
    what_it_does = forms.CharField(label="What happens in the app?", widget=forms.Textarea(attrs={"rows": 4,
                                   "placeholder": "e.g. The child points the phone at a letter card and a 3D animal jumps out, says its name..."}))
    audience = forms.CharField(label="Who is it for?", required=False,
                               widget=forms.TextInput(attrs={"placeholder": "e.g. Kids 3–8, and their parents; preschools"}))
    pricing = forms.CharField(label="Pricing", required=False,
                              widget=forms.TextInput(attrs={"placeholder": "e.g. 3 letters free, full unlock $2.99 one-time"}))
    languages = forms.CharField(label="Video language(s)", required=False, initial="English")
    goal = forms.ChoiceField(label="Main goal", choices=[("installs", "More installs"), ("purchases", "More purchases"),
                                                         ("awareness", "Brand awareness"), ("b2b", "Schools / business customers")])
    tone = forms.CharField(label="Tone", required=False, widget=forms.TextInput(attrs={"placeholder": "e.g. playful, magical, safe"}))
    brand_colour = forms.CharField(label="Brand colour", required=False, initial="#C2410C",
                                   widget=forms.TextInput(attrs={"type": "color"}))
    logo = forms.ImageField(label="Logo (PNG)", required=False)
    extra = forms.CharField(label="Anything else the agent should know?", required=False,
                            widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Things to always/never say, competitors, seasonal plans..."}))
    confirm_new = forms.BooleanField(label="Yes, this is a different app", required=False)


class MarketingSettingsForm(forms.Form):
    marketing = forms.BooleanField(label="Marketing agent works on this app", required=False)
    shorts_per_day = forms.TypedChoiceField(label="Shorts per day", coerce=int,
                                            choices=[(1, "1 (morning)"), (2, "2 (morning + afternoon)")])


class BriefReviewForm(forms.Form):
    brief = forms.CharField(widget=forms.Textarea(attrs={"rows": 28}))
    pillars = forms.CharField(label="Content pillars", widget=forms.Textarea(attrs={"rows": 7}),
                              help_text="One per line: Name | idea. Add “| letters” to rotate through A–Z.")
    visual_style = forms.CharField(label="Visual style for images and videos", required=False,
                                   widget=forms.Textarea(attrs={"rows": 4}))
