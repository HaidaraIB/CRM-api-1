from django.contrib import admin

from .models import (
    PbxArtifact,
    PbxExtension,
    PbxInboundRoute,
    PbxNode,
    PbxNumber,
    PbxOutboundRoute,
    PbxProvisioningJob,
    PbxTenant,
    PbxTrunk,
)

# Secrets (tokens, SIP secrets, artifact blobs) are excluded everywhere: they are
# encrypted at rest and must never be shown or edited through the admin.


@admin.register(PbxNode)
class PbxNodeAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "status", "max_tenants", "last_seen_at", "asterisk_version")
    exclude = ("node_token_hash", "crm_token_encrypted")


@admin.register(PbxTenant)
class PbxTenantAdmin(admin.ModelAdmin):
    list_display = ("company", "realm_key", "status", "home_node", "config_version", "applied_version", "outbound_suspended")
    list_filter = ("status", "outbound_suspended")
    readonly_fields = ("realm_key", "config_version", "applied_version", "last_apply_error")


@admin.register(PbxExtension)
class PbxExtensionAdmin(admin.ModelAdmin):
    list_display = ("company", "number", "display_name", "device_type", "user", "status")
    exclude = ("sip_secret_encrypted",)


@admin.register(PbxTrunk)
class PbxTrunkAdmin(admin.ModelAdmin):
    list_display = ("company", "name", "kind", "status", "host", "port", "transport")
    exclude = ("auth_secret_encrypted",)


@admin.register(PbxNumber)
class PbxNumberAdmin(admin.ModelAdmin):
    list_display = ("e164", "company", "trunk", "verified_at")


admin.site.register(PbxInboundRoute)
admin.site.register(PbxOutboundRoute)


@admin.register(PbxProvisioningJob)
class PbxProvisioningJobAdmin(admin.ModelAdmin):
    list_display = ("tenant", "kind", "status", "target_version", "attempts", "created_at", "finished_at")
    list_filter = ("status", "kind")


@admin.register(PbxArtifact)
class PbxArtifactAdmin(admin.ModelAdmin):
    list_display = ("tenant", "version", "action", "size_bytes", "sha256", "created_at")
    exclude = ("blob_encrypted",)
