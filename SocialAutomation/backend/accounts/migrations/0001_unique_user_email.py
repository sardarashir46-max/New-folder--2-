from django.conf import settings
from django.db import migrations, models
from django.db.models import Count, Q
from django.db.models.functions import Lower


CONSTRAINT_NAME = "auth_user_email_ci_uniq"


def _email_constraint():
    return models.UniqueConstraint(
        Lower("email"),
        condition=~Q(email=""),
        name=CONSTRAINT_NAME,
    )


def add_email_uniqueness(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    database = schema_editor.connection.alias

    duplicates = list(
        User._default_manager.using(database)
        .exclude(email="")
        .annotate(normalized_email=Lower("email"))
        .values("normalized_email")
        .annotate(user_count=Count("pk"))
        .filter(user_count__gt=1)
    )
    if duplicates:
        conflicting_user_ids = []
        for duplicate in duplicates:
            ids = (
                User._default_manager.using(database)
                .annotate(normalized_email=Lower("email"))
                .filter(normalized_email=duplicate["normalized_email"])
                .values_list("pk", flat=True)
            )
            conflicting_user_ids.extend(ids)

        raise RuntimeError(
            "Cannot enforce unique email addresses because existing user rows "
            "contain duplicates. No user accounts were changed. Resolve the "
            "email values for these user IDs, then rerun migrations: "
            f"{sorted(conflicting_user_ids)}"
        )

    features = schema_editor.connection.features
    if not (features.supports_expression_indexes and features.supports_partial_indexes):
        raise RuntimeError(
            "The configured database must support partial unique expression indexes "
            "to enforce case-insensitive email uniqueness."
        )

    schema_editor.add_constraint(User, _email_constraint())


def remove_email_uniqueness(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    schema_editor.remove_constraint(User, _email_constraint())


class Migration(migrations.Migration):
    # Run after auth's SQLite table alterations so they cannot remove this index.
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(add_email_uniqueness, remove_email_uniqueness)]
