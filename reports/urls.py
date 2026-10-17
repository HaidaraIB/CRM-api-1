from django.urls import path

from .views import SECTIONS, ReportLeadJourneyView, ReportLeadListView, ReportSectionView

urlpatterns = [
    path("lead-list/", ReportLeadListView.as_view(), name="reports_v2_lead_list"),
    path("leads/<int:client_id>/journey/", ReportLeadJourneyView.as_view(), name="reports_v2_lead_journey"),
    *[
        path(f"{section}/", ReportSectionView.as_view(), {"section": section}, name=f"reports_v2_{section.replace('-', '_')}")
        for section in SECTIONS
    ],
]
