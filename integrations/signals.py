"""Django signals for PBX / softphone integration."""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from accounts.models import User
from integrations.models import UserPbxExtension
from integrations.services.softphone_offboarding import offboard_softphone_user


@receiver(post_save, sender=User)
def softphone_offboard_deactivated_user(sender, instance: User, **kwargs):
    if instance.is_active:
        return
    offboard_softphone_user(instance, clear_sip_password=True)


@receiver(pre_delete, sender=UserPbxExtension)
def softphone_offboard_extension_delete(sender, instance: UserPbxExtension, **kwargs):
    offboard_softphone_user(instance.user, clear_sip_password=True, mapping=instance)
