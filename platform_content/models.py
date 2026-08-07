from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class GuideCategory(models.Model):
    """Category for grouping User Guide articles."""

    name_en = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_guide_categories"
        ordering = ["sort_order", "name_en"]
        verbose_name = "Guide Category"
        verbose_name_plural = "Guide Categories"

    def __str__(self):
        return self.name_en

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name_en) or "category"
            candidate = base
            n = 1
            while (
                GuideCategory.objects.filter(slug=candidate)
                .exclude(pk=self.pk)
                .exists()
            ):
                n += 1
                candidate = f"{base}-{n}"
            self.slug = candidate
        super().save(*args, **kwargs)


class GuideArticle(models.Model):
    """Platform-level Loop usage guide article (super admin CMS)."""

    title_en = models.CharField(max_length=255)
    title_ar = models.CharField(max_length=255)
    body_en = models.TextField()
    body_ar = models.TextField()
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    category = models.ForeignKey(
        GuideCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    cover_image = models.ImageField(
        upload_to="platform_content/guide/%Y/%m/",
        blank=True,
        null=True,
        max_length=500,
    )
    youtube_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional YouTube video URL (plays embedded in Loop).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_guide_articles"
        ordering = ["sort_order", "-updated_at"]
        verbose_name = "Guide Article"
        verbose_name_plural = "Guide Articles"

    def __str__(self):
        return self.title_en

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title_en) or "article"
            candidate = base
            n = 1
            while (
                GuideArticle.objects.filter(slug=candidate)
                .exclude(pk=self.pk)
                .exists()
            ):
                n += 1
                candidate = f"{base}-{n}"
            self.slug = candidate
        super().save(*args, **kwargs)


class NewsPost(models.Model):
    """Platform-level Loop news / updates post (super admin CMS)."""

    title_en = models.CharField(max_length=255)
    title_ar = models.CharField(max_length=255)
    summary_en = models.TextField(blank=True, default="")
    summary_ar = models.TextField(blank=True, default="")
    body_en = models.TextField()
    body_ar = models.TextField()
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When company owners were last notified about this post.",
    )
    last_notify_channels = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="Last notify channel choice: push, email, or both.",
    )
    cover_image = models.ImageField(
        upload_to="platform_content/news/%Y/%m/",
        blank=True,
        null=True,
        max_length=500,
    )
    youtube_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional YouTube video URL (plays embedded in Loop).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_news_posts"
        ordering = ["-published_at", "-created_at"]
        verbose_name = "News Post"
        verbose_name_plural = "News Posts"

    def __str__(self):
        return self.title_en

    def save(self, *args, **kwargs):
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        if not self.is_published:
            # Keep published_at if it was previously set so republish keeps history;
            # only clear when explicitly unpublished and never published before — leave as-is.
            pass
        super().save(*args, **kwargs)


class UserNewsReadState(models.Model):
    """Per-user cursor for unread Loop news (sidebar badge)."""

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="news_read_state",
    )
    last_read_at = models.DateTimeField(
        help_text="News published after this timestamp count as unread.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_user_news_read_states"
        verbose_name = "User News Read State"
        verbose_name_plural = "User News Read States"

    def __str__(self):
        return f"NewsReadState(user={self.user_id})"


class PageHelpVideo(models.Model):
    """Optional YouTube tutorial for a CRM page (shown as in-app embed)."""

    class PageKey(models.TextChoices):
        # Order = admin UI order (integrations grouped logically).
        WHATSAPP = "whatsapp", "WhatsApp"
        MESSAGING_CENTER = "messaging_center", "Messaging Center"
        CHATS = "chats", "Chats"
        META = "meta", "Meta"
        TIKTOK = "tiktok", "TikTok"
        TWILIO = "twilio", "Twilio / SMS"
        AI = "ai", "AI / OpenAI"
        LEAD_API = "lead_api", "Lead API"
        MUJEB = "mujeb", "Mujeb"
        PBX = "pbx", "PBX"

    page_key = models.CharField(
        max_length=64,
        unique=True,
        choices=PageKey.choices,
    )
    youtube_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="YouTube URL shown as an embedded tutorial on this page.",
    )
    title_en = models.CharField(max_length=255, blank=True, default="")
    title_ar = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_page_help_videos"
        ordering = ["page_key"]
        verbose_name = "Page Help Video"
        verbose_name_plural = "Page Help Videos"

    def __str__(self):
        return f"PageHelpVideo({self.page_key})"
