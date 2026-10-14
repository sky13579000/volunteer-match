from .dms import DmsCore
from .dms_admin import AdminMixin
from .dms_bulk import BulkMixin
from .dms_collab import CollabMixin
from .dms_docs import DocsMixin
from .dms_lifecycle import LifecycleMixin
from .dms_search import SearchMixin
from .dms_share import ShareMixin
from .dms_workflow import WorkflowMixin


class Dms(DocsMixin, WorkflowMixin, LifecycleMixin, CollabMixin, SearchMixin, ShareMixin, BulkMixin, AdminMixin, DmsCore):
    """Document management service: folders, metadata, versions, approval, retention, collaboration,
    external sharing, bulk operations, search and administration - all permission-checked per call."""
