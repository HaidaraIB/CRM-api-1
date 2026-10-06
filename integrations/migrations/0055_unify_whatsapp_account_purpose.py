import django.db.models.deletion
from django.db import migrations, models


def copy_inbox_numbers(apps, schema_editor):
    Inbox = apps.get_model("integrations", "WhatsAppInboxNumber")
    Account = apps.get_model("integrations", "WhatsAppAccount")
    Contact = apps.get_model("integrations", "SocialContact")
    Conversation = apps.get_model("integrations", "SocialConversation")
    Call = apps.get_model("integrations", "WhatsAppCall")
    ErrorLog = apps.get_model("integrations", "WhatsAppCallErrorLog")

    id_map = {}
    for row in Inbox.objects.all():
        existing = Account.objects.filter(phone_number_id=row.phone_number_id).first()
        if existing:
            id_map[row.id] = existing.id
            continue
        created = Account.objects.create(
            company_id=row.company_id,
            integration_account_id=row.integration_account_id,
            waba_id=row.waba_id,
            phone_number_id=row.phone_number_id,
            business_id=row.business_id or None,
            display_phone_number=row.display_phone_number or None,
            access_token=row.access_token,
            status=row.status,
            purpose="inbox",
            error_message=row.error_message,
            last_webhook_at=row.last_webhook_at,
            calling_enabled=row.calling_enabled,
            call_hours_enabled=row.call_hours_enabled,
            call_hours_timezone=row.call_hours_timezone or "",
            call_hours_weekly=row.call_hours_weekly or {},
            out_of_hours_message=row.out_of_hours_message or "",
        )
        id_map[row.id] = created.id

    for contact in Contact.objects.exclude(wa_inbox_number_id=None):
        new_id = id_map.get(contact.wa_inbox_number_id)
        if new_id:
            contact.inbox_account_id = new_id
            contact.save(update_fields=["inbox_account_id"])

    for conv in Conversation.objects.exclude(wa_inbox_number_id=None):
        new_id = id_map.get(conv.wa_inbox_number_id)
        if new_id:
            conv.inbox_account_id = new_id
            conv.save(update_fields=["inbox_account_id"])

    for call in Call.objects.exclude(wa_inbox_number_id=None):
        new_id = id_map.get(call.wa_inbox_number_id)
        if new_id and not call.whatsapp_account_id:
            call.whatsapp_account_id = new_id
            call.save(update_fields=["whatsapp_account_id"])

    for log in ErrorLog.objects.exclude(wa_inbox_number_id=None):
        new_id = id_map.get(log.wa_inbox_number_id)
        if new_id and not log.whatsapp_account_id:
            log.whatsapp_account_id = new_id
            log.save(update_fields=["whatsapp_account_id"])

    Call.objects.filter(whatsapp_account_id=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0054_inbox_whatsapp_calling_parity"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappaccount",
            name="purpose",
            field=models.CharField(
                choices=[("crm", "CRM"), ("inbox", "Inbox")],
                db_index=True,
                default="crm",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="whatsappaccount",
            name="error_message",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="whatsappaccount",
            name="last_webhook_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="whatsappaccount",
            name="display_phone_number",
            field=models.CharField(
                blank=True,
                help_text="رقم الهاتف المعروض للمستخدم",
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="whatsappaccount",
            index=models.Index(
                fields=["company", "purpose", "status"],
                name="whatsapp_acc_company_9c1a21_idx",
            ),
        ),
        migrations.AddField(
            model_name="socialcontact",
            name="inbox_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inbox_contacts_new",
                to="integrations.whatsappaccount",
            ),
        ),
        migrations.AddField(
            model_name="socialconversation",
            name="inbox_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inbox_conversations_new",
                to="integrations.whatsappaccount",
            ),
        ),
        migrations.RunPython(copy_inbox_numbers, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="socialcontact",
            name="social_contact_one_inbox_source",
        ),
        migrations.RemoveConstraint(
            model_name="socialconversation",
            name="social_conv_one_inbox_source",
        ),
        migrations.RemoveConstraint(
            model_name="socialconversation",
            name="uniq_social_conv_wa_inbox_contact",
        ),
        migrations.RemoveIndex(
            model_name="socialcontact",
            name="social_cont_wa_inbo_b76b89_idx",
        ),
        migrations.RemoveConstraint(
            model_name="whatsappcall",
            name="wa_call_exactly_one_sender",
        ),
        migrations.RemoveConstraint(
            model_name="whatsappcall",
            name="uniq_wa_call_inbox_meta_id",
        ),
        migrations.RemoveIndex(
            model_name="whatsappcall",
            name="integration_wa_inbo_f16f33_idx",
        ),
        migrations.RemoveField(model_name="socialcontact", name="wa_inbox_number"),
        migrations.RemoveField(model_name="socialconversation", name="wa_inbox_number"),
        migrations.RemoveField(model_name="whatsappcall", name="wa_inbox_number"),
        migrations.RemoveField(model_name="whatsappcallerrorlog", name="wa_inbox_number"),
        migrations.RenameField(
            model_name="socialcontact",
            old_name="inbox_account",
            new_name="wa_inbox_number",
        ),
        migrations.RenameField(
            model_name="socialconversation",
            old_name="inbox_account",
            new_name="wa_inbox_number",
        ),
        migrations.AlterField(
            model_name="socialcontact",
            name="wa_inbox_number",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inbox_contacts",
                to="integrations.whatsappaccount",
            ),
        ),
        migrations.AlterField(
            model_name="socialconversation",
            name="wa_inbox_number",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inbox_conversations",
                to="integrations.whatsappaccount",
            ),
        ),
        migrations.AddIndex(
            model_name="socialcontact",
            index=models.Index(
                fields=["wa_inbox_number", "external_id"],
                name="social_cont_wa_inbo_b76b89_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="socialcontact",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("connection__isnull", False), ("wa_inbox_number__isnull", True)),
                    models.Q(("connection__isnull", True), ("wa_inbox_number__isnull", False)),
                    _connector="OR",
                ),
                name="social_contact_one_inbox_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="socialconversation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("wa_inbox_number__isnull", False)),
                fields=("wa_inbox_number", "contact"),
                name="uniq_social_conv_wa_inbox_contact",
            ),
        ),
        migrations.AddConstraint(
            model_name="socialconversation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("connection__isnull", False), ("wa_inbox_number__isnull", True)),
                    models.Q(("connection__isnull", True), ("wa_inbox_number__isnull", False)),
                    _connector="OR",
                ),
                name="social_conv_one_inbox_source",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="whatsappcall",
            name="uniq_wa_call_account_meta_id",
        ),
        migrations.AlterField(
            model_name="whatsappcall",
            name="whatsapp_account",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="calls",
                to="integrations.whatsappaccount",
            ),
        ),
        migrations.AddConstraint(
            model_name="whatsappcall",
            constraint=models.UniqueConstraint(
                fields=("whatsapp_account", "meta_call_id"),
                name="uniq_wa_call_account_meta_id",
            ),
        ),
        migrations.DeleteModel(name="WhatsAppInboxNumber"),
    ]
