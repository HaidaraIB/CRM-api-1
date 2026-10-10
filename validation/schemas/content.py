from validation.registry import register_form
from validation.schema_dsl import max_length, required, rule, string_field


def _load():
    register_form(
        "guide_category.upsert",
        {
            "name_en": string_field("name", required(), max_length(120), ui_key="name", sample="Guides"),
            "name_ar": string_field("nameAr", required(), max_length(120), ui_key="nameAr", sample="أدلة"),
        },
        aliases={"name": "name_en", "nameAr": "name_ar"},
    )
    register_form(
        "guide_article.upsert",
        {
            "title_en": string_field("title", required(), max_length(255), ui_key="title", sample="Guide"),
            "title_ar": string_field("titleAr", required(), max_length(255), ui_key="titleAr", sample="دليل"),
            "body_en": string_field("body", required(), ui_key="body", sample="Article body"),
            "body_ar": string_field("bodyAr", required(), ui_key="bodyAr", sample="نص المقال"),
            "youtube_url": string_field(
                "youtubeUrl",
                rule("url", client_only=True),
                sample="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
        },
        aliases={"title": "title_en", "titleAr": "title_ar", "body": "body_en", "bodyAr": "body_ar", "youtubeUrl": "youtube_url"},
    )
    register_form(
        "news_post.upsert",
        {
            "title_en": string_field("title", required(), max_length(255), ui_key="title", sample="News"),
            "title_ar": string_field("titleAr", required(), max_length(255), ui_key="titleAr", sample="خبر"),
            "body_en": string_field("body", required(), ui_key="body", sample="Post body"),
            "body_ar": string_field("bodyAr", required(), ui_key="bodyAr", sample="نص الخبر"),
        },
        aliases={"title": "title_en", "titleAr": "title_ar", "body": "body_en", "bodyAr": "body_ar"},
    )
    register_form(
        "page_help_video.upsert",
        {
            "page_key": string_field("page", required(), max_length(128), sample="leads"),
            # PageHelpVideo.youtube_url is `blank=True, default=""` — "Optional
            # YouTube tutorial" per the model's own docstring.
            "youtube_url": string_field(
                "youtubeUrl",
                required(client_only=True),
                rule("url"),
                ui_key="youtubeUrl",
                sample="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
            "title_en": string_field("title", max_length(255), ui_key="title", sample="Help"),
        },
        aliases={"pageKey": "page_key", "youtubeUrl": "youtube_url", "title": "title_en"},
    )


_load()
