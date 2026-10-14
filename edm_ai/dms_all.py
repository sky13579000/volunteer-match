from .dms import DmsCore
from .dms_access import AccessMixin
from .dms_admin import AdminMixin
from .dms_bulk import BulkMixin
from .dms_calendar import CalendarMixin
from .dms_collab import CollabMixin
from .dms_docs import DocsMixin
from .dms_edit import EditMixin
from .dms_lifecycle import LifecycleMixin
from .dms_reports import ReportsMixin
from .dms_search import SearchMixin
from .dms_share import ShareMixin
from .dms_system import SystemMixin
from .dms_workflow import WorkflowMixin


class Dms(DocsMixin, WorkflowMixin, LifecycleMixin, CollabMixin, SearchMixin, ShareMixin, BulkMixin, CalendarMixin, EditMixin, AccessMixin, SystemMixin, ReportsMixin, AdminMixin, DmsCore):
    """Document management service: folders, metadata, versions, approval, retention, collaboration,
    external sharing, bulk operations, search and administration - all permission-checked per call."""
